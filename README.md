# Bazel PyPI Rules

## Setup

Add the following to your `WORKSPACE` file to add the external repositories:

```python
git_repository(
    name = "io_bazel_rules_python",
    # NOT VALID!  Replace this with a Git commit SHA.
    commit = "{HEAD}",
    remote = "https://github.com/joshclimacell/rules_python.git",
)

load("@io_bazel_rules_python//python:pip.bzl", "pip_repositories")

pip_repositories()
```

Then in your `BUILD` files load the PyPI rules with:

``` python
load("@io_bazel_rules_python//python:pip.bzl", "pip_import")

# This rule translates the specified requirements.txt into
# @my_deps//:requirements.bzl, which itself exposes a pip_install method.
pip_import(
   name = "my_deps",
   requirements = "//path/to:requirements.txt",
)

# Load the pip_install symbol for my_deps, and create the dependencies'
# repositories.
load("@my_deps//:requirements.bzl", "pip_install")
pip_install()
```

## Consuming PyPI dependencies

```python
load("@my_deps//:requirements.bzl", "pypi_requirements")

py_library(
    name = "mylib",
    srcs = ["mylib.py"],
    deps = [
        ":myotherlib",
        pypi_requirements(),
    ]
)
```

## Updating `tools/`

All of the content (except `BUILD`) under `tools/` is generated.  To update the
documentation simply run this in the root of the repository:

```shell
./update_tools.sh
```
