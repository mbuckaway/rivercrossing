# Shell Scripting Constitution - Google Shell Style Guide

## Based on Google Shell Style Guide

This document establishes the coding standards for all shell scripts, based on the Google Shell Style Guide.

## Core Philosophy

> "Shell should only be used for small utilities or simple wrapper scripts."

**Key Principles:**
- **Bash only** - Use Bash for all executable shell scripts
- **Simplicity** - Keep scripts under 100 lines when possible
- **Maintainability** - Code should be easily understood by others
- **Safety** - Use features that prevent common bugs

## When to Use Shell

### Appropriate Use Cases

**USE shell when:**
- Calling other utilities with minimal data manipulation
- Writing simple wrapper scripts
- Automating straightforward system tasks

**DO NOT use shell when:**
- Performance matters significantly
- Complex data manipulation is required
- Scripts exceed 500 lines
- Non-straightforward control flow logic is needed

**REQUIRED:** Rewrite to a structured language (Python, etc.) when complexity grows beyond these limits.

## Background

### Which Shell to Use

**REQUIRED:** Bash is the **only** shell scripting language permitted for executables.

**REQUIRED:** Start all executable scripts with `#!/usr/bin/env bash`

**WHY (shebang):** `env` resolves `bash` from `PATH`, so scripts run under a newer Bash
when one is installed (e.g. Homebrew) instead of being pinned to an old system Bash —
macOS ships only Bash 3.2 at `/bin/bash`, while tools such as SDKMAN require Bash 4+.
This is an intentional project deviation from the Google Shell Style Guide, which
prescribes `#!/bin/bash`.

**REQUIRED:** Use `set` to configure shell options rather than flags in shebang.

```bash
#!/usr/bin/env bash
set -uo pipefail  # Exit on error, undefined vars, pipe failures
```

**WHY:** Ensures consistency across all systems and avoids POSIX-compatibility constraints.

## Shell Files and Interpreter Invocation

### File Extensions

**REQUIRED:** Executables should have `.sh` extension or no extension.

**For build systems:** Use `.sh` extension (e.g., `foo.sh` with build rule `foo`)

**For PATH executables:** Prefer no extension (users don't need to know implementation language)

**REQUIRED:** Libraries must have `.sh` extension and should NOT be executable.

### SUID/SGID

**PROHIBITED:** SUID and SGID are **forbidden** on shell scripts.

**REQUIRED:** Use `sudo` to provide elevated access when needed.

## Environment

### STDOUT vs STDERR

**REQUIRED:** All error messages should go to `STDERR`.

```bash
# Good: Error function that writes to STDERR
err() {
  echo "[$(date +'%Y-%m-%dT%H:%M:%S%z')]: $*" >&2
}

if ! do_something; then
  err "Unable to do_something"
  exit 1
fi
```

## Comments

### File Header

**REQUIRED:** Start each file with a description of its contents.

```bash
#!/usr/bin/env bash
#
# Perform hot backups of Oracle databases.
```

**OPTIONAL:** Copyright notice and author information.

### Function Comments

**REQUIRED:** Any function that is not both obvious and short must have a comment.

**REQUIRED:** All library functions must have comments regardless of length.

**Format:**
```bash
#######################################
# Cleanup files from the backup directory.
# Globals:
#   BACKUP_DIR
#   ORACLE_SID
# Arguments:
#   None
# Returns:
#   0 on success, non-zero on error
#######################################
function cleanup() {
  # ...
}

#######################################
# Get configuration directory.
# Globals:
#   SOMEDIR
# Arguments:
#   None
# Outputs:
#   Writes location to stdout
#######################################
function get_dir() {
  echo "${SOMEDIR}"
}
```

### Implementation Comments

**REQUIRED:** Comment tricky, non-obvious, or important code sections.

**DO NOT:** Comment everything - focus on complex algorithms or unusual approaches.

### TODO Comments

**REQUIRED:** Use `TODO` for temporary or imperfect code.

**Format:** `TODO(username): Description (bug ####)`

```bash
# TODO(mrmonkey): Handle the unlikely edge cases (bug #1234)
```

## Formatting

### Indentation

**REQUIRED:** Indent **2 spaces**. No tabs.

**REQUIRED:** Use blank lines between blocks for readability.

**EXCEPTION:** Tabs allowed only in `<<-` here-documents.

```bash
# Good
if [[ "${condition}" ]]; then
  do_something
fi
```

### Line Length

**REQUIRED:** Maximum line length is **80 characters**.

**For long strings:** Use here-documents or embedded newlines.

```bash
# Good: Here document
cat <<END
I am an exceptionally long
string.
END

# Good: Embedded newline
long_string="I am an exceptionally
long string."

# Good: Long file path on its own line
long_file="/i/am/an/exceptionally/loooooooooooooooooooooong_file"
```

### Pipelines

**REQUIRED:** Split pipelines one per line if they don't fit on one line.

**REQUIRED:** Use 2-space indent for continuation.

```bash
# Good: All fits on one line
command1 | command2

# Good: Long commands
command1 \
  | command2 \
  | command3 \
  | command4
```

### Control Flow

**REQUIRED:** Put `; then` and `; do` on the same line as `if`, `for`, or `while`.

**REQUIRED:** `else` on its own line, closing statements (`fi`, `done`) vertically aligned.

```bash
# Good
local dir
for dir in "${dirs_to_cleanup[@]}"; do
  if [[ -d "${dir}/${SESSION_ID}" ]]; then
    log_date "Cleaning up old files in ${dir}/${SESSION_ID}"
    rm "${dir}/${SESSION_ID}/"* || error_message
  else
    mkdir -p "${dir}/${SESSION_ID}" || error_message
  fi
done

# Good: Always include 'in "$@"' for clarity
for arg in "$@"; do
  echo "argument: ${arg}"
done
```

### Case Statement

**REQUIRED:** Indent alternatives by 2 spaces.

**REQUIRED:** One-line alternatives need space after `)` and before `;;`.

**REQUIRED:** Multi-line actions split over separate lines.

```bash
# Good: Multi-line
case "${expression}" in
  a)
    variable="..."
    some_command "${variable}" "${other_expr}"
    ;;
  absolute)
    actions="relative"
    another_command "${actions}" "${other_expr}"
    ;;
  *)
    error "Unexpected expression '${expression}'"
    ;;
esac

# Good: One-line alternatives
while getopts 'abf:v' flag; do
  case "${flag}" in
    a) aflag='true' ;;
    b) bflag='true' ;;
    f) files="${OPTARG}" ;;
    v) verbose='true' ;;
    *) error "Unexpected option ${flag}" ;;
  esac
done
```

### Variable Expansion

**REQUIRED (priority order):**
1. Stay consistent with existing code
2. Quote your variables
3. Prefer `"${var}"` over `"$var"`

**Guidelines:**
- Don't brace-delimit single character shell specials/positional parameters unless necessary
- Brace-delimit all other variables

```bash
# Good: Special variables
echo "Positional: $1" "$5" "$3"
echo "Specials: !=$!, -=$-, _=$_"

# Good: Braces necessary
echo "many parameters: ${10}"

# Good: Other variables
echo "PATH=${PATH}, PWD=${PWD}, mine=${some_var}"
while read -r f; do
  echo "file=${f}"
done < <(find /tmp)

# Bad: Inconsistent style
echo a=$avar "b=$bvar" "PID=${$}" "${1}"
```

### Quoting

**REQUIRED:** Always quote:
- Strings containing variables, command substitutions, spaces, or shell meta characters
- Variables (unless careful unquoted expansion is required)

**REQUIRED:** Use arrays for safe quoting of lists.

**OPTIONAL:** Quote shell-internal readonly integers (`$?`, `$#`, `$$`, `$!`).

**REQUIRED:** Use `"$@"` unless you specifically need `$*`.

```bash
# Good: Quote command substitutions
flag="$(some_command and its args "$@" 'quoted separately')"

# Good: Quote variables
echo "${flag}"

# Good: Arrays with quoted expansion
declare -a FLAGS
FLAGS=( --foo --bar='baz' )
readonly FLAGS
mybinary "${FLAGS[@]}"

# Good: Don't quote literal integers
value=32

# Good: Quote command substitutions even for integers
number="$(generate_number)"

# Good: Quote shell meta characters
echo 'Hello stranger, and well met. Earn lots of $$$'

# Good: Passing arguments
# "$@" is right almost every time
function_call "$@"
```

## Features and Bugs

### ShellCheck

**REQUIRED:** Use [ShellCheck](https://www.shellcheck.net/) for all scripts.

ShellCheck identifies common bugs and warnings. Required for all scripts, large or small.

### Command Substitution

**REQUIRED:** Use `$(command)` instead of backticks.

```bash
# Good
var="$(command "$(command1)")"

# Bad
var="`command \`command1\``"
```

### Test, [ … ], and [[ … ]]

**REQUIRED:** `[[ … ]]` is preferred over `[ … ]`, `test`, and `/usr/bin/[`.

**WHY:** Reduces errors - no pathname expansion or word splitting between `[[` and `]]`.

```bash
# Good: Pattern matching
if [[ "filename" =~ ^[[:alnum:]]+name ]]; then
  echo "Match"
fi

# Good: Exact pattern match
if [[ "filename" == "f*" ]]; then
  echo "Match"
fi

# Bad: Unsafe with [
if [ "filename" == f* ]; then
  echo "Match"
fi
```

### Testing Strings

**REQUIRED:** Use quotes rather than filler characters.

**PREFERRED:** Use `-z` (zero length) and `-n` (non-zero length) for empty string tests.

```bash
# Good: Test for equality
if [[ "${my_var}" == "some_string" ]]; then
  do_something
fi

# Good: Test for empty string
if [[ -z "${my_var}" ]]; then
  do_something
fi

# Good: Test for non-empty
if [[ -n "${my_var}" ]]; then
  do_something
fi

# Bad: Filler characters
if [[ "${my_var}X" == "some_stringX" ]]; then
  do_something
fi
```

**REQUIRED:** Use `==` for equality (not `=`).

**REQUIRED:** Use `(( … ))` or `-lt`/`-gt` for numerical comparison (not `<`/`>` in `[[`).

```bash
# Good: Numerical comparison
if (( my_var > 3 )); then
  do_something
fi

if [[ "${my_var}" -gt 3 ]]; then
  do_something
fi

# Bad: Lexicographical comparison
if [[ "${my_var}" > 3 ]]; then
  # True for 4, false for 22
  do_something
fi
```

### Wildcard Expansion

**REQUIRED:** Use explicit path when doing wildcard expansion.

```bash
# Good: Explicit path
rm -v ./*

# Bad: Can delete files starting with -
rm -v *
```

### Eval

**PROHIBITED:** `eval` should be avoided.

Eval munges input and makes it impossible to check what variables were set.

### Arrays

**REQUIRED:** Use arrays for lists to avoid quoting issues.

**REQUIRED:** Use quoted expansion `"${array[@]}"`.

```bash
# Good: Array assignment and usage
declare -a flags
flags=(--foo --bar='baz')
flags+=(--greeting="Hello ${name}")
mybinary "${flags[@]}"

# Bad: String for sequences
flags='--foo --bar=baz'
mybinary ${flags}
```

**PROHIBITED:** Don't use strings for sequences or command expansions for arrays.

```bash
# Bad: Command expansion to array
declare -a files=($(ls /directory))

# Good: Use proper array assignment
readarray -t files < <(find /directory -type f)
```

### Pipes to While

**REQUIRED:** Use process substitution or `readarray` instead of piping to `while`.

**WHY:** Pipes create subshells - variables modified in pipeline don't propagate to parent.

```bash
# Bad: Subshell in pipe
last_line='NULL'
your_command | while read -r line; do
  last_line="${line}"
done
echo "${last_line}"  # Always outputs 'NULL'

# Good: Process substitution
last_line='NULL'
while read -r line; do
  last_line="${line}"
done < <(your_command)
echo "${last_line}"  # Outputs last line

# Good: readarray
readarray -t lines < <(your_command)
for line in "${lines[@]}"; do
  last_line="${line}"
done
```

### Arithmetic

**REQUIRED:** Always use `(( … ))` or `$(( … ))` rather than `let`, `$[ … ]`, or `expr`.

**PROHIBITED:** Never use `$[ … ]`, `expr`, or `let`.

```bash
# Good: Simple calculation
echo "$(( 2 + 2 )) is 4"

# Good: Comparison
if (( a < b )); then
  do_something
fi

# Good: Assignment
(( i = 10 * j + 400 ))

# Bad: Old syntax
i=$[2 * 10]
i=$( expr 4 + 4 )
let i="2 + 2"
```

**RECOMMENDED:** Omit `${var}` form inside `$(( … ))` for cleaner code.

```bash
# Good
local -i hundred="$(( 10 * 10 ))"
(( i += 3 ))
(( i -= 5 ))
```

### Aliases

**PROHIBITED:** Avoid aliases in scripts. Use functions instead.

```bash
# Bad: Alias
alias random_name="echo some_prefix_${RANDOM}"

# Good: Function
random_name() {
  echo "some_prefix_${RANDOM}"
}

fancy_ls() {
  ls -lh "$@"
}
```

## Naming Conventions

### Function Names

**REQUIRED:** Lower-case with underscores to separate words.

**REQUIRED:** For packages, separate with `::`.

**REQUIRED:** Braces on same line as function name.

**OPTIONAL:** Use `function` keyword consistently throughout project.

```bash
# Good: Single function
my_func() {
  # ...
}

# Good: Package function
mypackage::my_func() {
  # ...
}
```

### Variable Names

**REQUIRED:** Same as function names - lowercase with underscores.

```bash
# Good: Loop variables
for zone in "${zones[@]}"; do
  something_with "${zone}"
done
```

### Constants and Environment Variables

**REQUIRED:** All caps, separated with underscores, declared at top of file.

**REQUIRED:** Use `readonly` for constants.

```bash
# Good: Constant
readonly PATH_TO_FILES='/some/path'

# Good: Exported constant
declare -xr ORACLE_SID='PROD'

# Good: Set at runtime, made readonly immediately
ZIP_VERSION="$(dpkg --status zip | sed -n 's/^Version: //p')"
readonly ZIP_VERSION
```

### Source Filenames

**REQUIRED:** Lowercase with underscores if desired.

**Examples:** `maketemplate` or `make_template` (not `make-template`)

### Use Local Variables

**REQUIRED:** Declare function-specific variables with `local`.

**REQUIRED:** Separate declaration and assignment when using command substitution.

```bash
# Good: Separate lines
my_func() {
  local name="$1"

  local my_var
  my_var="$(my_func)"
  (( $? == 0 )) || return
}

# Bad: Combined - $? will be exit code of 'local', not my_func
my_func() {
  local my_var="$(my_func)"
  (( $? == 0 )) || return
}
```

### Function Location

**REQUIRED:** Put all functions together near top of file, just below constants.

**PROHIBITED:** Don't hide executable code between functions.

### main Function

**REQUIRED:** Use `main` function for scripts with multiple functions.

**REQUIRED:** Call `main "$@"` as last non-comment line.

```bash
#!/usr/bin/env bash

readonly CONSTANT='value'

function helper() {
  # ...
}

function main() {
  # Main program logic
  helper
}

main "$@"
```

**NOT REQUIRED:** For short, linear scripts where `main` is overkill.

## Calling Commands

### Checking Return Values

**REQUIRED:** Always check return values and give informative errors.

```bash
# Good: Check with if
if ! mv "${file_list[@]}" "${dest_dir}/"; then
  echo "Unable to move ${file_list[*]} to ${dest_dir}" >&2
  exit 1
fi

# Good: Check $?
mv "${file_list[@]}" "${dest_dir}/"
if (( $? != 0 )); then
  echo "Unable to move ${file_list[*]} to ${dest_dir}" >&2
  exit 1
fi
```

**For pipes:** Use `PIPESTATUS` to check individual commands.

```bash
# Good: Check whole pipe
tar -cf - ./* | ( cd "${dir}" && tar -xf - )
if (( PIPESTATUS[0] != 0 || PIPESTATUS[1] != 0 )); then
  echo "Unable to tar files to ${dir}" >&2
fi

# Good: Check individual pipe components
tar -cf - ./* | ( cd "${DIR}" && tar -xf - )
return_codes=( "${PIPESTATUS[@]}" )
if (( return_codes[0] != 0 )); then
  do_something
fi
if (( return_codes[1] != 0 )); then
  do_something_else
fi
```

### Builtin Commands vs External Commands

**PREFERRED:** Use shell builtins over external commands when available.

**WHY:** More efficient, robust, and portable.

```bash
# Good: Builtin parameter expansion
addition="$(( X + Y ))"
substitution="${string/#foo/bar}"
if [[ "${string}" =~ foo:(\d+) ]]; then
  extraction="${BASH_REMATCH[1]}"
fi

# Bad: External commands
addition="$(expr "${X}" + "${Y}")"
substitution="$(echo "${string}" | sed -e 's/^foo/bar/')"
```

## Consistency

**PRINCIPLE:** Use one style consistently throughout the codebase.

**PRIORITY:**
1. Technical correctness
2. Safety and bug prevention
3. Readability and maintainability
4. Consistency with existing code
5. Personal preference

**BALANCE:** Consider benefits of new styles while maintaining consistency. Don't use "consistency" to justify outdated patterns.

## Enforcement

### ShellCheck Integration

**REQUIRED:** All scripts must pass ShellCheck with no errors.

**RECOMMENDED:** Integrate ShellCheck into CI/CD pipeline.

```bash
# Run ShellCheck
shellcheck script.sh

# In CI/CD
find . -name '*.sh' -type f -exec shellcheck {} +
```

### Script Template

**RECOMMENDED:** Use this template for new scripts:

```bash
#!/usr/bin/env bash
#
# Brief description of script purpose
#
# Usage: script.sh [options] arguments

set -euo pipefail

# Constants
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"

# Functions
err() {
  echo "[$(date +'%Y-%m-%dT%H:%M:%S%z')]: $*" >&2
}

usage() {
  cat <<EOF
Usage: ${SCRIPT_NAME} [options] arguments

Description of what this script does.

Options:
  -h, --help     Show this help message
  -v, --verbose  Enable verbose output
EOF
}

main() {
  # Parse arguments
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help)
        usage
        exit 0
        ;;
      -v|--verbose)
        set -x
        shift
        ;;
      *)
        err "Unknown option: $1"
        usage
        exit 1
        ;;
    esac
  done

  # Main logic here
}

main "$@"
```

## Common Patterns

### Error Handling

```bash
# Exit on error with message
die() {
  echo "ERROR: $*" >&2
  exit 1
}

# Usage
[[ -f "${config_file}" ]] || die "Config file not found: ${config_file}"
```

### Logging

```bash
log() {
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*"
}

log "Starting process..."
```

### Cleanup on Exit

```bash
cleanup() {
  rm -rf "${temp_dir}"
}

trap cleanup EXIT

temp_dir="$(mktemp -d)"
```

### Checking Dependencies

```bash
check_dependencies() {
  local -a missing=()
  for cmd in "$@"; do
    if ! command -v "${cmd}" &> /dev/null; then
      missing+=("${cmd}")
    fi
  done

  if (( ${#missing[@]} > 0 )); then
    err "Missing required commands: ${missing[*]}"
    exit 1
  fi
}

check_dependencies git curl jq
```

## Summary of Key Rules

**DO:**
- ✅ Use `#!/usr/bin/env bash`
- ✅ Use 2-space indentation
- ✅ Keep lines under 80 characters
- ✅ Quote all variables: `"${var}"`
- ✅ Use `[[ ]]` for tests
- ✅ Use `$(( ))` for arithmetic
- ✅ Use arrays for lists
- ✅ Use `local` for function variables
- ✅ Check all return values
- ✅ Use ShellCheck
- ✅ Comment non-obvious code

**DON'T:**
- ❌ Use tabs for indentation
- ❌ Use backticks for command substitution
- ❌ Use `eval`
- ❌ Use `let`, `expr`, or `$[ ]`
- ❌ Use aliases in scripts
- ❌ Pipe to `while` (use process substitution)
- ❌ Write scripts over 100 lines (use Python instead)
- ❌ Use SUID/SGID on scripts

## References

- **Original Guide:** https://google.github.io/styleguide/shellguide.html
- **ShellCheck:** https://www.shellcheck.net/
- **Bash Manual:** https://www.gnu.org/software/bash/manual/
- **Advanced Bash Scripting Guide:** https://tldp.org/LDP/abs/html/

---

**Document Status:** Active
**Last Updated:** November 22, 2025
**Applies To:** All shell scripts (.devcontainer/, scripts/, */scripts/)
**Authority:** Google Shell Style Guide
**Authors:** Google (maintained by many Googlers)
