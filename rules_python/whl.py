# Copyright 2017 The Bazel Authors. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""The whl modules defines classes for interacting with Python packages."""

import argparse
import json
import os
import re
import shutil
import textwrap
import zipfile

import pkg_resources


class Wheel(object):
    def __init__(self, path):
        self._path = path

    def path(self):
        return self._path

    def basename(self):
        return os.path.basename(self.path())

    def distribution(self):
        # See https://www.python.org/dev/peps/pep-0427/#file-name-convention
        parts = self.basename().split('-')
        return parts[0]

    def version(self):
        # See https://www.python.org/dev/peps/pep-0427/#file-name-convention
        parts = self.basename().split('-')
        return parts[1]

    def repository_name(self):
        # Returns the canonical name of the Bazel repository for this package.
        canonical = 'pypi__{}_{}'.format(self.distribution(), self.version())
        # Escape any illegal characters with underscore.
        return re.sub('[-.]', '_', canonical)

    def _dist_info(self):
        # Return the name of the dist-info directory within the .whl file.
        # e.g. google_cloud-0.27.0-py2.py3-none-any.whl ->
        #      google_cloud-0.27.0.dist-info
        return '{}-{}.dist-info'.format(self.distribution(), self.version())

    def metadata(self):
        # Extract the structured data from metadata.json in the WHL's dist-info
        # directory.
        with zipfile.ZipFile(self.path(), 'r') as whl:
            # first check for metadata.json
            try:
                with whl.open(
                        self._dist_info() + '/metadata.json') as file_obj:
                    return json.loads(file_obj.read().decode("utf-8"))
            except KeyError:
                pass
            # fall back to METADATA file (https://www.python.org/dev/peps/pep-0427/)
            with whl.open(self._dist_info() + '/METADATA') as file_obj:
                return self._parse_metadata(file_obj.read().decode("utf-8"))

    def name(self):
        return self.metadata().get('name')

    def dependencies(self, extra=None):
        """Access the dependencies of this Wheel.

        Args:
          extra: if specified, include the additional dependencies of the named
            "extra".

        Yields:
          the names of requirements from the metadata.json
        """
        # TODO(mattmoor): Is there a schema to follow for this?
        run_requires = self.metadata().get('run_requires', [])
        for requirement in run_requires:
            if requirement.get('extra') != extra:
                # Match the requirements for the extra we're looking for.
                continue
            marker = requirement.get('environment')
            if marker and not pkg_resources.evaluate_marker(marker):
                # The current environment does not match the provided PEP 508 marker,
                # so ignore this requirement.
                continue
            requires = requirement.get('requires', [])
            for entry in requires:
                # Strip off any trailing versioning data.
                parts = re.split('[ ><=()]', entry)
                yield parts[0]

    def extras(self):
        return self.metadata().get('extras', [])

    def expand(self, directory):
        with zipfile.ZipFile(self.path(), 'r') as whl:
            whl.extractall(directory)

    # _parse_metadata parses METADATA files according to https://www.python.org/dev/peps/pep-0314/
    def _parse_metadata(self, content):
        # TODO: handle fields other than just name
        name_pattern = re.compile('Name: (.*)')
        return {'name': name_pattern.search(content).group(1)}


# pylint: disable=R0914
def main():
    parser = argparse.ArgumentParser(
        description='Unpack a WHL file as a py_library.')

    parser.add_argument(
        '--whl', action='store', help=('The .whl file we are expanding.'))

    parser.add_argument(
        '--requirements',
        action='store',
        help='The pip_import from which to draw dependencies.')

    parser.add_argument(
        '--directory',
        action='store',
        default='.',
        help='The directory into which to expand things.')

    parser.add_argument(
        '--extras',
        action='append',
        help='The set of extras for which to generate library targets.')
    args = parser.parse_args()
    whl = Wheel(args.whl)

    # Extract the files into the current directory
    whl.expand(args.directory)
    copied_whl_path = os.path.join(args.directory, os.path.basename(args.whl))
    shutil.copy(args.whl, copied_whl_path)

    join_str = ',\n        '
    dependencies = join_str.join(
        ['requirement("%s")' % d for d in whl.dependencies()])
    whl_dependencies = join_str.join(
        ['whl_requirement("%s")' % d for d in whl.dependencies()])

    extra_template = textwrap.dedent("""\
        py_library(
            name = "{extra}",
            deps = [
                ":pkg",{deps}
            ],
        )
    """)
    extras = '\n\n'.join([
        extra_template.format(
            extra=extra,
            deps=','.join(
                ['requirement("%s")' % dep
                 for dep in whl.dependencies(extra)]))
        for extra in args.extras or []
    ])

    whl_extra_template = textwrap.dedent("""\
        filegroup(
            name = "{extra}_whl",
            srcs = [
                ":whl",{deps}
            ],
        )
    """)
    whl_extras = '\n\n'.join([
        whl_extra_template.format(
            extra=extra,
            deps=','.join([
                'whl_requirement("%s")' % dep
                for dep in whl.dependencies(extra)
            ])) for extra in args.extras or []
    ])

    build_content = textwrap.dedent("""\
        package(default_visibility = ["//visibility:public"])

        load("{requirements}", "requirement", "whl_requirement")

        py_library(
            name = "pkg",
            srcs = glob(["**/*.py"]),
            data = glob(["**/*"], exclude=["**/*.py", "**/* *", "BUILD", "WORKSPACE"]),
            # This makes this directory a top-level in the python import
            # search path for anything that depends on this.
            imports = ["."],
            deps = [{dependencies}],
        )

        filegroup(
            name = "whl",
            srcs = glob(["**/*.whl"]) + [{whl_dependencies}],
        )

        {extras}

        {whl_extras}
    """).format(
        requirements=args.requirements,
        dependencies=dependencies,
        whl_dependencies=whl_dependencies,
        extras=extras,
        whl_path=copied_whl_path,
        whl_extras=whl_extras)

    with open(os.path.join(args.directory, 'BUILD'), 'w') as file_obj:
        file_obj.write(build_content)


if __name__ == '__main__':
    main()
