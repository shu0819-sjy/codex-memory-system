# Installation Guide

Codex Memory System is a local, file-based memory template. It requires Python 3.11 or newer and supports Windows, macOS, and Linux.

## 1. Clone the repository

```sh
git clone https://github.com/shu0819-sjy/codex-memory-system.git
cd codex-memory-system
```

## 2. Install the template

On Windows PowerShell:

```powershell
./codex-memory.ps1 install --non-interactive
```

On macOS or Linux:

```sh
sh ./codex-memory install --non-interactive
```

The portable Python entry point works on every supported platform:

```sh
python -m codex_memory install --non-interactive
```

The default destination is `~/.codex-memory` (`%USERPROFILE%\\.codex-memory` on Windows). Use `--root` to choose another destination. The first install refuses to overwrite an existing directory.

## 3. Check an installation

```sh
python -m codex_memory check
```

The check is read-only. Exit code `0` means success or warnings, `1` means a memory validation failure, and `2` means invalid arguments or a runtime error.

## 4. Update safely

```sh
python -m codex_memory install --update --non-interactive
```

Updates add only missing template paths and update `.template-version`. Existing memory files are preserved. Before changing version metadata, the installer attempts to create a `.template-version.backup-*` file. Keep that backup if an update fails and follow the error message to restore it manually after checking its contents.

## 5. Add the workflow rules

Merge the relevant parts of `AGENTS.example.md` into your own `AGENTS.md` and replace `<MEMORY_ROOT>` with the actual destination. The CLI can print platform-specific instructions without writing a file:

```sh
python -m codex_memory instructions
```

The tool does not edit `AGENTS.md`, model settings, permissions, network settings, or other Codex configuration.

## Privacy and uninstall

The repository contains templates only. Do not publish personal memory files, archives, logs, secrets, or machine-specific absolute paths. The checker reads local files only and does not upload content.

There is no forced uninstall command. Back up your memory first, inspect the target directory, and remove it manually only when you are sure it contains no unrelated files. Avoid recursive wildcard deletion.

## Development checks

```sh
python -m compileall -q codex_memory tests hooks
python -m unittest discover -v
python -m codex_memory check --root memory
```

CI covers Windows, macOS, Ubuntu, and Python 3.11 through 3.14. Hosted runners are not a substitute for checking your own target machine.
