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
"""The piptool module imports pip requirements into Bazel rules."""

import argparse
import atexit
import os
import pkgutil
# import pkg_resources
import shutil
import sys
import tempfile
import textwrap

# Note: We carefully import the following modules in a particular
# order, since these modules modify the import path and machinery.
import pkg_resources

if sys.version_info < (3, 0):
    _WHL_LIBRARY_RULE = 'whl_library'
else:
    _WHL_LIBRARY_RULE = 'whl3_library'


def _extract_packages(package_names):
    """Extract zipfile contents to disk and add to import path"""

    # Set a safe extraction dir
    extraction_tmpdir = tempfile.mkdtemp()
    atexit.register(
        lambda: shutil.rmtree(extraction_tmpdir, ignore_errors=True))
    pkg_resources.set_extraction_path(extraction_tmpdir)

    # Extract each package to disk
    dirs_to_add = []
    for package_name in package_names:
        req = pkg_resources.Requirement.parse(package_name)
        extraction_dir = pkg_resources.resource_filename(req, '')
        dirs_to_add.append(extraction_dir)

    # Add extracted directories to import path ahead of their zip file
    # counterparts.
    sys.path[0:0] = dirs_to_add
    existing_pythonpath = os.environ.get('PYTHONPATH')
    if existing_pythonpath:
        dirs_to_add.extend(existing_pythonpath.split(':'))
    os.environ['PYTHONPATH'] = ':'.join(dirs_to_add)


# Wheel, pip, and setuptools are much happier running from actual
# files on disk, rather than entries in a zipfile.  Extract zipfile
# contents, add those contents to the path, then import them.
_extract_packages(['pip', 'setuptools', 'wheel'])

# Defeat pip's attempt to mangle sys.path
_SAVED_SYS_PATH = sys.path
sys.path = sys.path[:]
import pip  # pylint: disable=C0413
sys.path = _SAVED_SYS_PATH

# import setuptools
# import wheel


def _pip_main(argv):
    # Extract the certificates from the PAR following the example of get-pip.py
    # https://github.com/pypa/get-pip/blob/430ba37776ae2ad89/template.py#L164-L168
    cert_path = os.path.join(tempfile.mkdtemp(), "cacert.pem")
    with open(cert_path, "wb") as cert:
        cert.write(pkgutil.get_data("pip._vendor.requests", "cacert.pem"))
    argv = ["--disable-pip-version-check", "--cert", cert_path] + argv
    return pip.main(argv)


from rules_python.whl import Wheel  # pylint: disable=C0413


def main():
    args = _parse_args()

    # https://github.com/pypa/pip/blob/9.0.1/pip/__init__.py#L209
    if _pip_main(["wheel", "-w", args.directory, "-r", args.input]):
        sys.exit(1)

    # Enumerate the .whl files we downloaded.
    def list_whl_files():
        dir_ = args.directory + '/'
        for root, unused_dirnames, filenames in os.walk(dir_):
            for fname in filenames:
                if fname.endswith('.whl'):
                    yield os.path.join(root, fname)

    wheels = [Wheel(path) for path in list_whl_files()]

    bzl_file_content = _make_bzl_file_content(
        wheels=wheels,
        reqs_repo_name=args.name,
        input_requirements_file_path=args.input)
    with open(args.output, 'w') as file_obj:
        file_obj.write(bzl_file_content)


def _parse_args():
    parser = argparse.ArgumentParser(
        description='Import Python dependencies into Bazel.')
    parser.add_argument(
        '--name', action='store', help='The namespace of the import.')
    parser.add_argument(
        '--input', action='store', help='The requirements.txt file to import.')
    parser.add_argument(
        '--output',
        action='store',
        help='The requirements.bzl file to export.')
    parser.add_argument(
        '--directory',
        action='store',
        help='The directory into which to put .whl files.')
    return parser.parse_args()


def _make_bzl_file_content(wheels, reqs_repo_name,
                           input_requirements_file_path):
    wheel_to_extras = _make_wheel_to_extras(wheels)

    join_str = ',\n    '
    pypi_name_to_py_library = join_str.join([
        join_str.join([
            '"{pypi_name}": "@{wheel_name}//:pkg"'.format(
                pypi_name=wheel.distribution().lower(),
                wheel_name=_make_wheel_name(reqs_repo_name, wheel))
        ] + [
            # For every extra that is possible from this requirements.txt
            '"{pypi_name}[{extra}]": "@{wheel_name}//:{extra}"'.format(
                pypi_name=wheel.distribution().lower(),
                extra=extra.lower(),
                wheel_name=_make_wheel_name(reqs_repo_name, wheel))
            for extra in wheel_to_extras.get(wheel, [])
        ]) for wheel in wheels
    ])

    pypi_name_to_whl_filegroup = join_str.join([
        join_str.join([
            '"{pypi_name}": "@{wheel_name}//:whl"'.format(
                pypi_name=wheel.distribution().lower(),
                wheel_name=_make_wheel_name(reqs_repo_name, wheel))
        ] + [
            # For every extra that is possible from this requirements.txt
            '"{pypi_name}[{extra}]": "@{wheel_name}//:{extra}_whl"'.format(
                pypi_name=wheel.distribution().lower(),
                extra=extra.lower(),
                wheel_name=_make_wheel_name(reqs_repo_name, wheel))
            for extra in wheel_to_extras.get(wheel, [])
        ]) for wheel in wheels
    ])

    merged_whl_repo_name = "{reqs_repo_name}_merged".format(
        reqs_repo_name=reqs_repo_name)
    merged_py_library = '"@{merged_whl_repo_name}//:pkg"'.format(
        merged_whl_repo_name=merged_whl_repo_name)
    merged_whl_filegroup = '"@{merged_whl_repo_name}//:whl"'.format(
        merged_whl_repo_name=merged_whl_repo_name)

    if wheels:
        whl_library_rule_list = []
        for wheel in wheels:
            extras = ','.join(
                ['"%s"' % extra for extra in wheel_to_extras.get(wheel, [])])
            whl_library_rule = _make_whl_library_rule(
                reqs_repo_name=reqs_repo_name,
                whl_repo_name=_make_wheel_name(reqs_repo_name, wheel),
                wheels=[wheel],
                extras=extras)
            whl_library_rule_list.append(whl_library_rule)
        whl_library_rules = '\n'.join(whl_library_rule_list)

        merged_whl_library_rule = _make_whl_library_rule(
            reqs_repo_name=reqs_repo_name,
            whl_repo_name=merged_whl_repo_name,
            wheels=wheels,
            extras='')
    else:
        whl_library_rules = 'pass'

    return _populate_bzl_template(
        input_requirements_file_path=input_requirements_file_path,
        whl_library_rules=whl_library_rules,
        pypi_name_to_py_library=pypi_name_to_py_library,
        pypi_name_to_whl_filegroup=pypi_name_to_whl_filegroup,
        merged_whl_library_rule=merged_whl_library_rule,
        merged_py_library=merged_py_library,
        merged_whl_filegroup=merged_whl_filegroup)


def _make_wheel_to_extras(wheels):
    """Determines the list of possible "extras" for each .whl file.

    The possibility of an extra is determined by looking at its
    additional requirements, and determinine whether they are
    satisfied by the complete list of available wheels.

    Args:
        wheels: a list of Wheel objects

    Returns:
        a dict that is keyed by the Wheel objects in wheels, and whose
        values are lists of possible extras.
    """
    pypi_name_to_wheel = {wheel.distribution(): wheel for wheel in wheels}

    # TODO(mattmoor): Consider memoizing if this recursion ever becomes
    # expensive enough to warrant it.
    def is_possible(pypi_name, extra):
        pypi_name = pypi_name.replace("-", "_")
        # If we don't have the .whl at all, then this isn't possible.
        if pypi_name not in pypi_name_to_wheel:
            return False
        wheel = pypi_name_to_wheel[pypi_name]
        # If we have the .whl, and we don't need anything extra then
        # we can satisfy this dependency.
        if not extra:
            return True
        # If we do need something extra, then check the extra's
        # dependencies to make sure they are fully satisfied.
        for extra_dep in wheel.dependencies(extra=extra):
            req = pkg_resources.Requirement.parse(extra_dep)
            # Check that the dep and any extras are all possible.
            if not is_possible(req.project_name, None):
                return False
            for extra_ in req.extras:
                if not is_possible(req.project_name, extra_):
                    return False
        # If all of the dependencies of the extra are satisfiable then
        # it is possible to construct this dependency.
        return True

    return {
        wheel: [
            extra for extra in wheel.extras()
            if is_possible(wheel.distribution(), extra)
        ]
        for wheel in wheels
    }


_WHL_LIBRARY_RULE_TEMPLATE = """\
  if "{whl_repo_name}" not in native.existing_rules():
    {whl_library}(
        name = "{whl_repo_name}",
        whls = [{whls}],
        requirements = "@{reqs_repo_name}//:requirements.bzl",
        extras = [{extras}]
    )
"""


def _make_whl_library_rule(reqs_repo_name, whl_repo_name, wheels, extras):
    whls = ', '.join([
        '"@{name}//:{path}"'.format(
            name=reqs_repo_name, path=wheel.basename()) for wheel in wheels
    ])
    # Indentation here matters.  whl_library must be within the scope
    # of the function below.  We also avoid reimporting an existing WHL.
    return """
  if "{whl_repo_name}" not in native.existing_rules():
    {whl_library}(
        name = "{whl_repo_name}",
        whls = [{whls}],
        requirements = "@{reqs_repo_name}//:requirements.bzl",
        extras = [{extras}]
    )""".format(
        whl_repo_name=whl_repo_name,
        reqs_repo_name=reqs_repo_name,
        extras=extras,
        whl_library=_WHL_LIBRARY_RULE,
        whls=whls)


_BZL_TEMPLATE = textwrap.dedent("""\
    # Install pip requirements.
    #
    # Generated from {input}

    load("@io_bazel_rules_python//python:whl.bzl", "{whl_library}")

    def pip_install():
        {whl_library_rules}
        {merged_whl_library_rule}

    _requirements = {{
        {pypi_name_to_py_library}
    }}

    _whl_requirements = {{
        {pypi_name_to_whl_filegroup}
    }}

    _merged_py_library = {merged_py_library}
    _merged_whl_filegroup = {merged_whl_filegroup}

    def pypi_requirements():
        return _merged_py_library

    def pypi_whl_requirements():
        return _merged_whl_filegroup

    def pypi_whl_requirement(name):
        name_key = _make_name_key(name)
        if name_key not in _whl_requirements:
            fail("Could not find pip-provided whl dependency: '%s'; available: %s" % (name, sorted(_whl_requirements.keys())))
        return _whl_requirements[name_key]

    # Deprecated; don't use.
    def requirement(name):
        name_key = _make_name_key(name)
        if name_key not in _requirements:
            fail("Could not find pip-provided dependency: '%s'; available: %s" % (name, sorted(_requirements.keys())))
        return _requirements[name_key]

    def _make_name_key(name):
        name_key = name.replace("-", "_").lower()
        return name_key
""")


def _populate_bzl_template(input_requirements_file_path, whl_library_rules,
                           pypi_name_to_py_library, pypi_name_to_whl_filegroup,
                           merged_whl_library_rule, merged_py_library,
                           merged_whl_filegroup):
    return _BZL_TEMPLATE.format(
        input=input_requirements_file_path,
        whl_library_rules=whl_library_rules,
        pypi_name_to_py_library=pypi_name_to_py_library,
        pypi_name_to_whl_filegroup=pypi_name_to_whl_filegroup,
        whl_library=_WHL_LIBRARY_RULE,
        merged_whl_library_rule=merged_whl_library_rule,
        merged_py_library=merged_py_library,
        merged_whl_filegroup=merged_whl_filegroup)


def _make_wheel_name(namespace, wheel):
    return "{}_{}".format(namespace, wheel.repository_name())


if __name__ == '__main__':
    main()
