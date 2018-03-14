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


# pylint: disable=R0914
def main():
    args = _parse_args()

    dependency_list = []
    whl_dependency_list = []
    extra_list = []
    whl_extra_list = []

    whl_paths = args.whl_paths
    if args.whl is not None:
        whl_paths = whl_paths + [args.whl]

    # Extract the files into the current directory.
    for wheel_path in args.whl_paths:
        wheel = Wheel(wheel_path)
        wheel.expand(args.directory)

        copied_whl_path = os.path.join(args.directory,
                                       os.path.basename(wheel_path))
        shutil.copy(wheel_path, copied_whl_path)

        if args.track_deps:
            for dependency in wheel.dependencies():
                dependency_list.append('requirement("{}")'.format(dependency))
                whl_dependency_list.append(
                    'pypi_whl_requirement("{}")'.format(dependency))
            for extra in args.extras:
                extra_list.append(_make_extra(extra, wheel))
                whl_extra_list.append(_make_whl_extra(extra, wheel))

    # Generate BUILD file.
    dependency_join_str = ',\n        '
    extras_join_str = '\n\n'

    dependencies = dependency_join_str.join(dependency_list)
    whl_dependencies = dependency_join_str.join(whl_dependency_list)
    extras = extras_join_str.join(extra_list)
    whl_extras = extras_join_str.join(whl_extra_list)

    build_file_content = _make_build_file_content(
        requirements_bzl=args.requirements,
        dependencies=dependencies,
        whl_dependencies=whl_dependencies,
        extras=extras,
        whl_extras=whl_extras)

    with open(os.path.join(args.directory, 'BUILD'), 'w') as file_obj:
        file_obj.write(build_file_content)


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


def _parse_args():
    parser = argparse.ArgumentParser(
        description='Unpack a .whl file as a py_library.')

    parser.add_argument(
        '--whl_paths',
        action='append',
        default=[],
        help=('The .whl files we are expanding.'))

    parser.add_argument(
        '--whl',
        action='store',
        default=None,
        help='Deprecated; use --whl_paths')

    parser.add_argument('--track_deps', action='store', type=bool)

    parser.add_argument(
        '--requirements',
        action='store',
        default=None,
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

    return parser.parse_args()


_EXTRA_TEMPLATE = textwrap.dedent("""\
    py_library(
        name = "{extra}",
        deps = [
            ":pkg",{deps}
        ],
    )
""")
_WHL_EXTRA_TEMPLATE = textwrap.dedent("""\
    filegroup(
        name = "{extra}_whl",
        srcs = [
            ":whl",{deps}
        ],
    )
""")


def _make_extra(extra, wheel):
    return _EXTRA_TEMPLATE.format(
        extra=extra,
        deps=','.join(
            ['requirement("%s")' % dep for dep in wheel.dependencies(extra)]),
    )


def _make_whl_extra(extra, wheel):
    _WHL_EXTRA_TEMPLATE.format(
        extra=extra,
        deps=','.join([
            'pypi_whl_requirement("%s")' % dep
            for dep in wheel.dependencies(extra)
        ]),
    )


def _make_build_file_content(requirements_bzl, dependencies, whl_dependencies,
                             extras, whl_extras):
    if requirements_bzl:
        template = (
            'load("{requirements_bzl}", "requirement", "pypi_whl_requirement")'
        )
        load_requirements_statement = template.format(
            requirements_bzl=requirements_bzl)
    else:
        load_requirements_statement = ''

    return textwrap.dedent("""\
        package(default_visibility = ["//visibility:public"])

        {load_requirements_statement}

        py_library(
            name = "pkg",
            srcs = glob(["**/*.py"]),
            data = glob(["**/*"], exclude=["**/*.py", "**/* *", "BUILD", "WORKSPACE", "**/*.whl"]),
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
        requirements_bzl=requirements_bzl,
        dependencies=dependencies,
        whl_dependencies=whl_dependencies,
        extras=extras,
        whl_extras=whl_extras,
        load_requirements_statement=load_requirements_statement)


if __name__ == '__main__':
    main()
