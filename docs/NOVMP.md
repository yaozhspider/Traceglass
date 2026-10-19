# NoVmp

NoVmp is installed as `novmp` and `NoVmp` in the analysis and REA images.
The upstream revision is `6c23c9a335f70e8d5ed6299668fd802f2314c896`
(2021-05-05), from <https://github.com/can1357/NoVmp>.
Run it through the analysis workspace's existing Bash tool.

```sh
novmp --help
novmp --version
mkdir -p scratch/novmp
cp samples/unpacked.exe scratch/novmp/unpacked.exe
novmp scratch/novmp/unpacked.exe
```

The input must be an **unpacked x64 PE** using VMProtect 3.0–3.5, according
to upstream's stated scope. A packed file must first be unpacked or dumped.
For a dump, specify the original image base when necessary:

```sh
novmp scratch/novmp/unpacked.exe -base 0x140000000 -vms 0x729B81
```

`-vms` selects VMEnter RVAs; `-sections` adds VMProtect section names;
`-noopt` disables optimization; `-opt:constant` enables constant-obfuscation
optimization. `-experimental:recompile` is upstream's experimental x64
recompiler and should be used only on a disposable copy.

NoVmp creates `vms/` beside its input. Recompilation may also create a
`*.devirt.*` file. Use a writable working copy. The REA workspace bind mount
remains read-only; a copy in its container scratch directory is required
there. Installation does not add a dedicated MCP method.

Upstream unconditionally calls the Windows `pause` command at the end. On Linux
it can print `pause: not found` after completing its work while returning exit
code 0; use the actual analysis output to assess the result.

The installed binary uses its own pinned C/C++ dependencies. It does not
replace Python Capstone in either environment. Source, dependency revisions,
build files and smoke-test evidence are retained under
`traceglass-data/novmp-20261015/`. License notices are in `/opt/novmp/`.

The installation smoke test parses a self-authored unprotected PE and checks
normal exit and output-directory handling. It does not establish successful
devirtualization of an arbitrary VMProtect-protected program.
