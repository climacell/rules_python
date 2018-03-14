# Copyright 2017 Google Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Import .whl files into Bazel."""

def _whl_impl_base(repository_ctx, python_binary):
    """Core implementation of whl_library."""
    whl_path_args = []
    for wheel_path in repository_ctx.attr.whls:
        wheel_path = repository_ctx.path(wheel_path)
        whl_path_args += ['--whl_paths', wheel_path]

    whl_args = []
    if repository_ctx.attr.whl != None:
        whl_args += ['--whl', repository_ctx.path(repository_ctx.attr.whl)]
    
    if not (whl_path_args or whl_args):
        fail("One of `whl` or `whls` must be provided")

    args = [
        python_binary,
        repository_ctx.path(repository_ctx.attr._script),
    ]
    args += whl_path_args
    args += whl_args
    if repository_ctx.attr.extras:
        args += ["--extras=%s" % extra for extra in repository_ctx.attr.extras]
    if repository_ctx.attr.requirements:
        args += ["--requirements", repository_ctx.attr.requirements]

    result = repository_ctx.execute(args, quiet=False)
    if result.return_code:
        fail("whl_library failed: %s (%s)" % (result.stdout, result.stderr))

def _whl3_impl(repository_ctx):
    return _whl_impl_base(repository_ctx, "python3")

def _whl_impl(repository_ctx):
    return _whl_impl_base(repository_ctx, "python")

whl_library = repository_rule(
    attrs = {
        "whls": attr.label_list(
            allow_files = True,
            doc = "List of .whl files that this library encompasses",
        ),
        "whl": attr.label(
            allow_files = True,
            doc = "A single .whl file that this library encompasses",
        ),
        "requirements": attr.string(),
        "extras": attr.string_list(),
        "_script": attr.label(
            executable = True,
            default = Label("//tools:whltool.par"),
            cfg = "host",
        ),
    },
    implementation = _whl_impl,
)

whl3_library = repository_rule(
    attrs = {
        "whls": attr.label_list(
            allow_files = True,
            doc = "List of .whl files that this library encompasses",
        ),
        "whl": attr.label(
            allow_files = True,
            doc = "A single .whl file that this library encompasses",
        ),
        "requirements": attr.string(),
        "extras": attr.string_list(),
        "_script": attr.label(
            executable = True,
            default = Label("//tools:whltool.par"),
            cfg = "host",
        ),
    },
    implementation = _whl3_impl,
)

"""A rule for importing <code>.whl</code> dependencies into Bazel.

<b>This rule is currently used to implement <code>pip_import</code>,
it is not intended to work standalone, and the interface may change.</b>
See <code>pip_import</code> for proper usage.

This rule imports a <code>.whl</code> file as a <code>py_library</code>:
<pre><code>whl_library(
    name = "foo",
    whls = [":my-whl-file", ...],
    requirements = "<name of pip_import rule>",
)
</code></pre>

This rule defines a <code>@foo//:pkg</code> <code>py_library</code> target and
a <code>@foo//:whl</code> <code>filegroup</code> target.

Args:
  whls: The paths to the .whl files (the names are expected to follow [this
    convention](https://www.python.org/dev/peps/pep-0427/#file-name-convention))

  requirements: The name of the pip_import repository rule from which to
    load each <code>.whl</code>'s dependencies.

  extras: A subset of the "extras" available from these <code>.whl</code>s for
    which <code>requirements</code> has the dependencies.
"""
