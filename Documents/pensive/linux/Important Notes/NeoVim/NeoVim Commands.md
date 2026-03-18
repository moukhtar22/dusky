# Neovim — Permanent Reference

> [!note]
> **Scope:** stock Neovim behavior, accurate as of **March 2026**.  
> This note focuses on **core editor features** that work without plugins: modes, motions, text objects, visual/block editing, search/substitute, macros, registers, buffers/windows/tabs, file navigation, quickfix, and diff mode.
>
> For long-term readability, this note uses **full option names** where practical (`ignorecase`, `hlsearch`, `incsearch`) instead of terse abbreviations (`ic`, `hls`, `is`).

> [!important]
> The central Neovim mental model is:
>
> - **`count + command`** → repeat a command
> - **`operator + motion`** → act over a range
> - **`operator + text object`** → act on a semantic unit
> - **`.`** → repeat the **last change**
>
> Examples:
>
> - `3j` → move down 3 lines
> - `d2w` → delete 2 words
> - `ci"` → change inside double quotes
> - `cgn` → change next search match, then `.` to repeat on further matches

---

## 1. Minimal setup on Arch Linux

### Install Neovim and useful CLI dependencies

```bash
sudo pacman -S neovim wl-clipboard ripgrep ctags
```

### Why these packages matter

- `neovim` — the editor
- `wl-clipboard` — reliable system clipboard integration on **Wayland** (`"+` register, `clipboard=unnamedplus`)
- `ripgrep` — fast project-wide search for `:grep`/quickfix workflows
- `ctags` — tag-based symbol navigation with `Ctrl-]` / `:tag`

> [!note]
> Under Wayland, `"+` clipboard integration is most reliable when `wl-copy` and `wl-paste` are available via `wl-clipboard`.  
> Neovim may also use terminal clipboard features such as OSC 52 in supported terminals, but `wl-clipboard` remains the standard local provider.

### Recommended minimal options

#### Lua (`init.lua`)

```lua
vim.opt.number = true
vim.opt.relativenumber = true
vim.opt.ignorecase = true
vim.opt.smartcase = true
vim.opt.hlsearch = true
vim.opt.incsearch = true
vim.opt.clipboard = "unnamedplus"
vim.opt.undofile = true
```

#### Vimscript (`init.vim`)

```vim
set number
set relativenumber
set ignorecase
set smartcase
set hlsearch
set incsearch
set clipboard=unnamedplus
set undofile
```

---

## 2. Key notation and modes

### Key notation

| Notation | Meaning |
|---|---|
| `<Esc>` | Escape |
| `<CR>` | Enter / Return |
| `<Tab>` | Tab |
| `<BS>` | Backspace |
| `<C-x>` | Ctrl+x |
| `<S-Tab>` | Shift+Tab |
| `<Leader>` | User-defined leader key; not used in this stock reference |

### Modes

| Mode | Purpose |
|---|---|
| **Normal** | Navigation, editing, operators, motions, commands |
| **Insert** | Typing text |
| **Visual** | Selecting text |
| **Select** | Rare; selection mode similar to GUI editors |
| **Command-line** | `:` commands, `/` and `?` searches |
| **Replace** | Overwrite characters (`R`) |
| **Terminal** | Interact with a terminal buffer |
| **Operator-pending** | Temporary mode after commands like `d`, `c`, `y`, `>` |

> [!tip]
> Press **`<Esc>`** to return to **Normal mode** from most modes.  
> The main exception is **Terminal mode**, where the escape sequence is **`<C-\><C-n>`**.

---

## 3. Start, open, save, quit

### Start Neovim

```bash
nvim
nvim file.txt
nvim file1 file2
nvim +42 file.txt          # open at line 42
nvim +'/pattern' file.txt  # open at first match
nvim -o file1 file2        # horizontal splits
nvim -O file1 file2        # vertical splits
nvim -p file1 file2        # tabs
nvim -d file1 file2        # diff mode
```

### File commands

| Command | Meaning |
|---|---|
| `:edit file` or `:e file` | Open file in current buffer |
| `:enew` | New empty buffer |
| `:saveas newname` | Save current buffer as a new file and switch to it |
| `:write` or `:w` | Save current file |
| `:update` | Save only if modified |
| `:wall` or `:wa` | Save all modified buffers |
| `:quit` or `:q` | Quit current window |
| `:quit!` or `:q!` | Quit without saving |
| `:wq` | Save and quit |
| `:x` | Save **only if modified**, then quit |
| `ZZ` | Normal-mode equivalent of `:x` |
| `ZQ` | Normal-mode equivalent of `:q!` |
| `:qall` or `:qa` | Quit everything |
| `:qall!` or `:qa!` | Force-quit everything |
| `:wqall` or `:wqa` | Save all and quit |

> [!warning]
> `:x` and `ZZ` are **not** the same as `:wq` in a subtle but important way:  
> they write only when the buffer is modified.

---

## 4. Core movement

## 4.1 Character and line movement

| Command | Meaning |
|---|---|
| `h` `j` `k` `l` | Left / down / up / right |
| `0` | Beginning of line, column 0 |
| `^` | First non-blank character on line |
| `$` | End of line |
| `g_` | Last non-blank character on line |
| `+` | First non-blank on next line |
| `-` | First non-blank on previous line |
| `<Enter>` | First non-blank on next line |

### Wrapped-line movement

If `wrap` is enabled, screen lines and file lines differ.

| Command | Meaning |
|---|---|
| `gj` | Down by displayed screen line |
| `gk` | Up by displayed screen line |
| `g0` | Start of displayed line |
| `g$` | End of displayed line |
| `gm` | Middle of displayed line |

## 4.2 Word movement

### Lowercase vs uppercase word motions

- Lowercase word motions (`w`, `b`, `e`) use **punctuation-aware** word boundaries.
- Uppercase word motions (`W`, `B`, `E`) treat any run of non-whitespace as a WORD.

| Command | Meaning |
|---|---|
| `w` | Start of next word |
| `b` | Start of previous word |
| `e` | End of current/next word |
| `ge` | End of previous word |
| `W` `B` `E` `gE` | Same, but WORD-based |

## 4.3 Find a character on the current line

| Command | Meaning |
|---|---|
| `f<char>` | Jump **to** next occurrence of `<char>` |
| `F<char>` | Jump backward **to** previous occurrence |
| `t<char>` | Jump **before** next occurrence |
| `T<char>` | Jump backward **after** previous occurrence |
| `;` | Repeat last `f/F/t/T` in same direction |
| `,` | Repeat in opposite direction |

Example:

- `dt,` → delete until just before the next comma
- `cf)` → change up to and including the next `)`

## 4.4 Move through file structure

| Command | Meaning |
|---|---|
| `gg` | First line of file |
| `G` | Last line of file |
| `{count}G` | Go to line number |
| `:{count}` | Go to line number from command line |
| `%` | Jump to matching delimiter/item under cursor |
| `(` `)` | Previous / next sentence |
| `{` `}` | Previous / next paragraph |
| `H` `M` `L` | Top / middle / bottom of window |

> [!warning]
> `%` jumps between matching items such as `()`, `{}`, `[]`.  
> `d%` deletes **from the cursor to the match, inclusive**.  
> It does **not** mean “delete inside brackets”. For “inside”, use text objects like `di(`, `di{`, `di[`.

## 4.5 Scroll and recenter

| Command | Meaning |
|---|---|
| `<C-d>` | Half-page down |
| `<C-u>` | Half-page up |
| `<C-f>` | Full page forward |
| `<C-b>` | Full page backward |
| `<C-e>` | Scroll view down one line |
| `<C-y>` | Scroll view up one line |
| `zz` | Recenter cursor line |
| `zt` | Put cursor line at top |
| `zb` | Put cursor line at bottom |

---

## 5. Insert, append, open, replace

## 5.1 Enter Insert mode

| Command | Meaning |
|---|---|
| `i` | Insert before cursor |
| `a` | Append after cursor |
| `I` | Insert at first non-blank on line |
| `A` | Append at end of line |
| `o` | Open new line below |
| `O` | Open new line above |
| `gi` | Return to last insert position and enter Insert mode |

## 5.2 Replace mode

| Command | Meaning |
|---|---|
| `r<char>` | Replace one character |
| `R` | Enter Replace mode |
| `gR` | Enter virtual replace mode |

> [!note]
> `gR` is niche. Most users rely on `r`, `R`, or normal change commands (`c`, `s`, `S`, `ciw`, etc.).

---

## 6. Editing operators and high-value edits

## 6.1 The operator grammar

Many powerful edits are composed as:

```text
operator + motion
operator + text object
```

Common operators:

| Operator | Meaning |
|---|---|
| `d` | Delete |
| `c` | Change |
| `y` | Yank |
| `>` | Indent right |
| `<` | Indent left |
| `=` | Reindent / format according to indent rules |
| `gq` | Format text |
| `!` | Filter through external command |

Examples:

- `dw` → delete a word
- `c$` → change to end of line
- `yi"` → yank inside double quotes
- `>ap` → indent a paragraph
- `gqap` → format a paragraph
- `!ip sort` → filter inner paragraph through `sort`

## 6.2 Delete / change / substitute

| Command | Meaning |
|---|---|
| `x` | Delete character under cursor |
| `X` | Delete character before cursor |
| `dd` | Delete current line |
| `{count}dd` | Delete N lines |
| `D` | Delete to end of line |
| `dw` | Delete to start of next word |
| `db` | Delete backward to start of previous word |
| `diw` | Delete inner word |
| `daw` | Delete a word including surrounding spacing |
| `cc` | Change current line |
| `C` | Change to end of line |
| `cw` | Change word from cursor |
| `ciw` | Change inner word |
| `caw` | Change a word including surrounding spacing |
| `s` | Substitute one character and enter Insert mode |
| `S` | Substitute whole line (similar to `cc`) |

> [!important]
> For day-to-day editing, **text-object forms** such as `ciw`, `diw`, `ci"`, `da(` are usually more precise than raw motion forms such as `cw` or `dw`.

> [!note]
> `cw` has a classic Vim behavior quirk: when the cursor is on a non-blank within a word, `cw` behaves more like `ce` than `dw`.  
> If exact boundaries matter, prefer **text objects** such as `ciw`.

## 6.3 Yank and put

| Command | Meaning |
|---|---|
| `yy` or `Y` | Yank current line |
| `{count}yy` | Yank N lines |
| `yw` | Yank to next word |
| `yiw` | Yank inner word |
| `p` | Put after cursor / below current line |
| `P` | Put before cursor / above current line |

### Characterwise vs linewise paste

- If the yanked/deleted text is **characterwise**, `p` puts **after** the cursor and `P` puts **before**.
- If it is **linewise** (`yy`, `dd`), `p` puts **below** the current line and `P` puts **above**.

## 6.4 Undo, redo, and repeat

| Command | Meaning |
|---|---|
| `u` | Undo |
| `<C-r>` | Redo |
| `U` | Undo all latest changes on the current line |
| `.` | Repeat the last change |

> [!warning]
> `U` is a niche line-level undo command.  
> It is **not** a general redo and is **not** the main undo mechanism. Use `u` and `<C-r>` for normal undo/redo.

> [!important]
> `.` repeats the **last change**, not the last motion, not the last search, and **not the last macro**.

## 6.5 Join, indent, format, case, numbers

### Join lines

| Command | Meaning |
|---|---|
| `J` | Join next line with a space adjustment |
| `gJ` | Join next line without inserting/removing spaces |

### Indentation

| Command | Meaning |
|---|---|
| `>>` | Indent current line right |
| `<<` | Indent current line left |
| `{count}>>` | Indent N lines |
| `=%` | Reindent to matching delimiter region |
| `==` | Reindent current line |
| `gg=G` | Reindent entire file |

### Formatting

| Command | Meaning |
|---|---|
| `gqap` | Format a paragraph |
| `gqq` | Format current line |
| `gwap` | Format paragraph, preserving cursor position more gently |

### Change case

| Command | Meaning |
|---|---|
| `~` | Toggle case of character under cursor |
| `guu` | Lowercase line |
| `gUU` | Uppercase line |
| `guiw` | Lowercase inner word |
| `gUiw` | Uppercase inner word |
| `g~iw` | Toggle case of inner word |

### Increment / decrement numbers

| Command | Meaning |
|---|---|
| `<C-a>` | Increment number at/after cursor |
| `<C-x>` | Decrement number at/after cursor |

---

## 7. Text objects

Text objects are one of Neovim’s most important features.

### `i` vs `a`

In operator-pending or Visual mode:

- `i` = **inner**
- `a` = **around**

Examples:

- `ciw` → change inner word
- `daw` → delete a word plus surrounding spacing
- `yi"` → yank inside double quotes
- `va(` → visually select around parentheses

### Common text objects

| Text object | Meaning |
|---|---|
| `iw` / `aw` | inner word / a word |
| `iW` / `aW` | inner WORD / a WORD |
| `is` / `as` | inner sentence / a sentence |
| `ip` / `ap` | inner paragraph / a paragraph |
| `i"` / `a"` | inside / around double quotes |
| `i'` / `a'` | inside / around single quotes |
| `` i` `` / `` a` `` | inside / around backticks |
| `i(` / `a(` | inside / around parentheses |
| `i[` / `a[` | inside / around brackets |
| `i{` / `a{` | inside / around braces |
| `i<` / `a<` | inside / around angle brackets |
| `ib` / `ab` | alias for parentheses block |
| `iB` / `aB` | alias for braces block |

### High-value examples

```text
ciw    change current word
di"    delete inside double quotes
ya(    yank around parentheses
va{    visually select around braces
dap    delete a paragraph
gqap   format a paragraph
```

---

## 8. Visual mode and blockwise “vertical editing”

## 8.1 Enter Visual mode

| Command | Meaning |
|---|---|
| `v` | Characterwise Visual mode |
| `V` | Linewise Visual mode |
| `<C-v>` | Blockwise Visual mode |

> [!tip]
> If `<C-v>` is intercepted by the terminal or environment, try **`<C-q>`** as an alternative for blockwise Visual mode.

Once in Visual mode, use motions to grow or shrink the selection, then apply an operator:

- `d` delete
- `y` yank
- `c` change
- `>` indent
- `<` outdent
- `=` reindent
- `gq` format

## 8.2 Visual-mode essentials

| Command | Meaning |
|---|---|
| `o` | Move cursor to opposite end of selection |
| `O` | In blockwise Visual mode, move to opposite corner |
| `gv` | Reselect the last visual selection |

### Useful Visual workflows

| Command | Meaning |
|---|---|
| `v...y` | Select and yank |
| `V...d` | Delete selected lines |
| `V...>` | Indent selected lines |
| `V...<` | Outdent selected lines |
| `V...= ` | Reindent selected lines |

> [!tip]
> After shifting indentation in Visual mode, use:
>
> - `>gv` to indent and keep the selection
> - `<gv` to outdent and keep the selection

## 8.3 Blockwise Visual mode (`<C-v>`) — vertical editing

This is Neovim’s built-in answer to many “multi-cursor” tasks.

### Core block operations

| Command | Meaning |
|---|---|
| `<C-v>` + motion | Select a rectangular block |
| `Itext<Esc>` | Insert `text` at the start of each selected line |
| `Atext<Esc>` | Append `text` at the end of each selected line |
| `ctext<Esc>` | Replace the selected block with `text` |
| `d` / `x` | Delete the selected block |
| `r<char>` | Replace each selected character with `<char>` |

> [!important]
> In blockwise mode, commands like `I`, `A`, and `c` apply to **all selected lines only after pressing `<Esc>`**.

### Vertical-editing recipes

#### Prefix multiple lines

Given:

```text
alpha
beta
gamma
```

Add `# ` to each line:

1. Move to the first character of `alpha`
2. Press `<C-v>jj`
3. Press `I# <Esc>`

Result:

```text
# alpha
# beta
# gamma
```

#### Append a semicolon to several lines

1. Move to first target line
2. Press `<C-v>` and select the lines
3. Move to line ends with `$`
4. Press `A;<Esc>`

#### Delete a column

1. Use `<C-v>` to select the column
2. Press `d`

#### Replace a column with the same text

1. Select block with `<C-v>`
2. Press `cNEW<Esc>`

### Number sequences in Visual mode

| Command | Meaning |
|---|---|
| `<C-a>` | Increment selected numbers |
| `g<C-a>` | Increment selected numbers sequentially |
| `<C-x>` | Decrement selected numbers |
| `g<C-x>` | Decrement selected numbers sequentially |

---

## 9. Search

## 9.1 Basic search commands

| Command | Meaning |
|---|---|
| `/pattern` | Search forward |
| `?pattern` | Search backward |
| `n` | Next match in same direction |
| `N` | Previous match in opposite direction |
| `*` | Search forward for word under cursor |
| `#` | Search backward for word under cursor |
| `g*` | Search forward for partial word under cursor |
| `g#` | Search backward for partial word under cursor |
| `:nohlsearch` or `:noh` | Clear search highlighting |

> [!note]
> `*` and `#` search for the **whole keyword** under the cursor.  
> `g*` and `g#` are less strict and allow partial-word matches.

## 9.2 Search-related options

| Command | Meaning |
|---|---|
| `:set ignorecase` | Ignore case in search |
| `:set noignorecase` | Disable ignore case |
| `:set smartcase` | Override ignorecase when uppercase is used |
| `:set hlsearch` | Highlight matches |
| `:set nohlsearch` | Disable search highlighting |
| `:set incsearch` | Show matches incrementally while typing |

### Recommended combination

```vim
:set ignorecase
:set smartcase
:set hlsearch
:set incsearch
```

This gives:

- case-insensitive search by default
- case-sensitive search when the pattern contains uppercase
- visible matches
- live incremental feedback

## 9.3 Useful regex atoms for search and substitute

| Pattern | Meaning |
|---|---|
| `\<` | Start of word |
| `\>` | End of word |
| `\c` | Force case-insensitive match |
| `\C` | Force case-sensitive match |
| `\V` | Very nomagic: treat most characters literally |
| `.` | Any character |
| `^` | Start of line |
| `$` | End of line |

### Examples

```vim
/\<word\>         " whole word
/\Vpath/to/file   " literal search with slashes
/\cfoo            " force case-insensitive
/\CBar            " force case-sensitive
```

---

## 10. Substitute (`:s`) and multi-occurrence replacement

## 10.1 Command shape

```vim
:[range]s/pattern/replacement/[flags]
```

### Common ranges

| Range | Meaning |
|---|---|
| *(none)* | Current line |
| `%` | Whole file |
| `10,20` | Lines 10 through 20 |
| `.,$` | Current line to end of file |
| `'<,'>` | Current visual selection lines |

### Common flags

| Flag | Meaning |
|---|---|
| `g` | Replace all matches in each line |
| `c` | Confirm each replacement |
| `i` | Ignore case for this substitute |
| `I` | Match case for this substitute |
| `e` | Suppress “pattern not found” error |
| `n` | Report number of matches only; do not change |

## 10.2 Essential substitute commands

| Command | Meaning |
|---|---|
| `:s/old/new/` | Replace first match on current line |
| `:s/old/new/g` | Replace all matches on current line |
| `:%s/old/new/g` | Replace all matches in whole file |
| `:%s/old/new/gc` | Replace all matches in whole file with confirmation |
| `:10,20s/old/new/g` | Replace on lines 10–20 |
| `:'<,'>s/old/new/g` | Replace in selected lines |

### Add text at beginning or end of lines

| Command | Meaning |
|---|---|
| `:%s/^/prefix/` | Prefix every line |
| `:%s/$/suffix/` | Suffix every line |
| `:'<,'>s/^/prefix/` | Prefix selected lines |
| `:'<,'>s/$/suffix/` | Suffix selected lines |

### Use a different delimiter when `/` is inconvenient

```vim
:%s#/old/path#/new/path#g
```

## 10.3 Replace the same word everywhere

### Whole-file replacement with confirmation

If the word is `count`:

```vim
:%s/\<count\>/total/gc
```

This is the most direct stock Neovim solution.

### Interactive same-word replacement with `cgn`

This is one of the most efficient built-in workflows.

1. Put cursor on the word
2. Press `*` to search for that word
3. Press `cgn`
4. Type replacement text
5. Press `<Esc>`
6. Press `.` repeatedly for the next matches

Example:

```text
*       search current word
cgnNEW<Esc>
. . .   repeat on next matches
```

> [!important]
> `cgn` changes the **next search match** and sets up `.` perfectly for repeated interactive replacement.

### Select next match visually

| Command | Meaning |
|---|---|
| `gn` | Select next search match in Visual mode |
| `gN` | Select previous search match in Visual mode |
| `cgn` | Change next search match |
| `dgn` | Delete next search match |
| `ygn` | Yank next search match |

## 10.4 Restrict substitution to the Visual area only

A plain `:'<,'>s/.../.../g` works on the **selected lines**, not necessarily only the selected columns/chars.

To restrict a substitution to the remembered Visual area, use `\%V`:

```vim
:'<,'>s/\%Vfoo/bar/g
```

This is especially useful after characterwise or blockwise selection.

## 10.5 Repeat substitute patterns efficiently

### Reuse the last search pattern

```vim
:%s//replacement/g
```

If the last search was `/foo`, this becomes `:%s/foo/replacement/g`.

### Delete matches by substituting with empty text

```vim
:%s/foo//g
```

## 10.6 The `:global` command for structural batch edits

`global` runs an Ex command on every line matching a pattern.

### Examples

```vim
:g/error/d
:v/error/d
:g/^/normal! I# 
```

Meaning:

- `:g/error/d` → delete lines containing `error`
- `:v/error/d` → delete lines **not** containing `error`
- `:g/^/normal! I# ` → prefix every line with `# `

> [!warning]
> `:normal` obeys mappings.  
> `:normal!` uses raw built-in keys and is usually safer in scripts and batch edits.

---

## 11. Registers and the clipboard

Deletes, yanks, and changes all interact with registers.

## 11.1 Important registers

| Register | Meaning |
|---|---|
| `""` | Unnamed register |
| `"0` | Last yank |
| `"1`–`"9` | Delete history |
| `"-` | Small delete register |
| `"a`–`"z` | Named registers |
| `"A`–`"Z` | Append to named register |
| `"_` | Black-hole register |
| `"+` | System clipboard |
| `"*` | Selection clipboard/provider-dependent |

### Inspect registers

```vim
:registers
```

## 11.2 Use a specific register

Prefix a yank/delete/put with `"register`.

Examples:

```vim
"ayy      " yank line into register a
"ap       " put register a
"+yy      " yank line to system clipboard
"+p       " paste from system clipboard
```

## 11.3 Prevent deletes from clobbering your yank

Use the black-hole register:

```vim
"_daw
"_x
```

This deletes without overwriting the unnamed register.

## 11.4 Paste over a Visual selection without losing the original yank

A classic pattern:

```vim
"_dP
```

Meaning:

1. Delete selection into black-hole register
2. Paste original unnamed register before cursor

Useful when replacing text repeatedly.

## 11.5 Insert register contents while typing

In Insert mode:

| Command | Meaning |
|---|---|
| `<C-r>a` | Insert contents of register `a` |
| `<C-r>0` | Insert last yank |
| `<C-r>"` | Insert unnamed register |
| `<C-r>+` | Insert system clipboard |

---

## 12. Macros

Macros automate repetitive edits by recording keystrokes into a register.

## 12.1 Record and play back

| Command | Meaning |
|---|---|
| `qa` | Start recording into register `a` |
| `q` | Stop recording |
| `@a` | Play macro from register `a` |
| `@@` | Repeat last executed macro |
| `{count}@a` | Run macro N times |
| `qA` | Append to macro in register `a` |

### Basic workflow

1. `qa`
2. Perform edits
3. `q`
4. `@a`

## 12.2 Practical macro advice

- Build macros to be **position-independent** when possible
- Use **searches**, **text objects**, and **motions** inside the macro
- Test on one line first
- Then apply with a count or over a selection

## 12.3 Apply a macro to many lines

### By count

```text
10@a
```

Run macro `a` 10 times.

### Over a Visual line selection

1. Select lines with `V`
2. Run:

```vim
:'<,'>normal! @a
```

This executes the macro on each selected line.

> [!warning]
> `.` does **not** replay the macro.  
> It repeats only the **last change made by the macro**, which is often not what is intended.

---

## 13. Marks, jumps, jumplist, changelist

## 13.1 Marks

| Command | Meaning |
|---|---|
| `ma` | Set mark `a` |
| `'a` | Jump to mark `a` at line start |
| `` `a `` | Jump to mark `a` at exact position |
| `mA` | Uppercase mark, file-global |
| `:marks` | List marks |

### Useful built-in marks

| Mark | Meaning |
|---|---|
| `` `. `` | Exact position of last change |
| `'[` / `` `[ `` | Start of last changed or yanked text |
| `']` / `` `] `` | End of last changed or yanked text |
| `''` / `` `` `` | Previous jump position |

## 13.2 Jumplist

| Command | Meaning |
|---|---|
| `<C-o>` | Older position in jumplist |
| `<C-i>` | Newer position in jumplist |
| `:jumps` | Show jumplist |

> [!note]
> In many terminals, `<C-i>` and `<Tab>` produce the same keycode.  
> Neovim still uses the jumplist concept, but terminal behavior may affect how `<C-i>` is distinguished.

## 13.3 Changelist

| Command | Meaning |
|---|---|
| `g;` | Older position in changelist |
| `g,` | Newer position in changelist |
| `:changes` | Show changelist |

This is extremely useful for revisiting recent edit locations.

---

## 14. Buffers, windows, and tabs

> [!important]
> These are different things:
>
> - **Buffer** = an open file or text object in memory
> - **Window** = a viewport showing a buffer
> - **Tab page** = a layout containing one or more windows
>
> Tabs are **not** the same as files.

## 14.1 Buffers

| Command | Meaning |
|---|---|
| `:buffers` or `:ls` | List buffers |
| `:buffer N` or `:b N` | Switch to buffer number N |
| `:bnext` or `:bn` | Next buffer |
| `:bprevious` or `:bp` | Previous buffer |
| `:bfirst` | First buffer |
| `:blast` | Last buffer |
| `:b#` | Alternate buffer |
| `<C-^>` | Switch to alternate buffer |
| `:bdelete` or `:bd` | Delete current buffer |
| `:bwipeout` | Wipe buffer completely |

### High-value buffer shortcuts

- `:ls` → list buffers
- `:b#` or `<C-^>` → toggle between current and previous buffer

## 14.2 Windows (splits)

### Open splits

| Command | Meaning |
|---|---|
| `:split` or `:sp` | Horizontal split |
| `:vsplit` or `:vs` | Vertical split |
| `:new` | New empty buffer in split |
| `:vnew` | New empty buffer in vertical split |

### Move between windows

| Command | Meaning |
|---|---|
| `<C-w>h` | Left window |
| `<C-w>j` | Lower window |
| `<C-w>k` | Upper window |
| `<C-w>l` | Right window |
| `<C-w>w` | Next window |
| `<C-w>p` | Previous window |

### Manage windows

| Command | Meaning |
|---|---|
| `<C-w>q` | Quit current window |
| `<C-w>o` | Keep only current window |
| `<C-w>x` | Exchange current window with another |
| `<C-w>r` / `<C-w>R` | Rotate windows |
| `<C-w>H` `J` `K` `L` | Move current window to far left/bottom/top/right |
| `<C-w>=` | Equalize split sizes |

### Resize windows

| Command | Meaning |
|---|---|
| `<C-w>+` | Increase height |
| `<C-w>-` | Decrease height |
| `<C-w>>` | Increase width |
| `<C-w><` | Decrease width |
| `<C-w>_` | Maximize height |
| `<C-w>\|` | Maximize width |
| `:resize 20` | Set height |
| `:vertical resize 80` | Set width |

## 14.3 Tab pages

| Command | Meaning |
|---|---|
| `:tabnew` | New tab |
| `:tabedit file` | Open file in new tab |
| `gt` | Next tab |
| `gT` | Previous tab |
| `{count}gt` | Go to tab number |
| `:tabs` | List tabs |
| `:tabclose` | Close current tab |
| `:tabonly` | Close all other tabs |

---

## 15. File and project navigation

## 15.1 `gf` / `gF` — go to file under cursor

If the cursor is on a path or filename:

| Command | Meaning |
|---|---|
| `gf` | Edit file under cursor |
| `gF` | Edit file under cursor and jump to line number if present |
| `<C-w>f` | Open file under cursor in a split |
| `<C-w>F` | Open file under cursor in a split and use line number |

Examples of text `gF` understands well:

```text
src/main.c:120
README.md:5
```

## 15.2 File search with `:find`

`gf` works on a literal path under the cursor.  
`:find` searches using the `'path'` option.

### Useful configuration

```lua
vim.opt.path:append("**")
```

or

```vim
set path+=**
```

Then:

```vim
:find init.lua
:sfind init.lua
:tabfind init.lua
```

Meaning:

- `:find` → open in current window
- `:sfind` → open in split
- `:tabfind` → open in new tab

## 15.3 Tags-based symbol navigation

Generate tags in a project:

```bash
ctags -R .
```

Then in Neovim:

| Command | Meaning |
|---|---|
| `Ctrl-]` | Jump to tag under cursor |
| `:tag name` | Jump to tag |
| `:tselect name` | Choose among multiple matching tags |
| `Ctrl-t` | Jump back in tag stack |
| `:tags` | Show tag stack |

This is a plugin-free way to navigate code.

## 15.4 Quickfix and grep workflows

Quickfix is Neovim’s built-in list for search results, compiler output, and batch edits.

### Built-in project search

#### Using `:vimgrep` (portable, built in)

```vim
:vimgrep /pattern/gj **/*.lua
:copen
```

Flags in `vimgrep`:

- `g` → all matches per file
- `j` → do not jump to first match immediately

#### Using `:grep` with ripgrep

Recommended configuration:

```lua
vim.opt.grepprg = "rg --vimgrep --smart-case"
vim.opt.grepformat = "%f:%l:%c:%m"
```

Then:

```vim
:grep pattern
:copen
```

### Quickfix commands

| Command | Meaning |
|---|---|
| `:copen` | Open quickfix window |
| `:cclose` | Close quickfix window |
| `:cwindow` | Open quickfix only if non-empty |
| `:cnext` or `:cn` | Next quickfix entry |
| `:cprevious` or `:cp` | Previous quickfix entry |
| `:cfirst` | First entry |
| `:clast` | Last entry |

### Location list equivalents

| Command | Meaning |
|---|---|
| `:lopen` | Open location list |
| `:lclose` | Close location list |
| `:lnext` | Next location-list entry |
| `:lprevious` | Previous location-list entry |

## 15.5 Batch edits across multiple files

### Use the argument list

```vim
:args **/*.py
:argdo %s/\<foo\>/bar/ge | update
```

- `:args` sets the argument list
- `:argdo` runs a command for each file in the argument list
- `update` writes only if modified

### Use quickfix results

After `:vimgrep` or `:grep`:

```vim
:cfdo %s/\<foo\>/bar/ge | update
```

- `:cdo` → run once per quickfix **entry**
- `:cfdo` → run once per **file** in the quickfix list

For file-wide replacements, `:cfdo` is usually what is wanted.

---

## 16. Diff mode and file comparison

Yes — Neovim can compare files directly.

## 16.1 Start diff mode

```bash
nvim -d file1 file2
nvim -d LOCAL BASE REMOTE MERGED
```

> [!note]
> Some systems also provide a `vimdiff` command alias, but `nvim -d ...` is the portable form to rely on.

## 16.2 Start diff mode from inside Neovim

| Command | Meaning |
|---|---|
| `:diffsplit otherfile` | Diff current window against `otherfile` |
| `:vert diffsplit otherfile` | Vertical diff split |
| `:diffthis` | Mark current window for diff |
| `:diffoff` | Disable diff in current window |
| `:diffoff!` | Disable diff in all windows |
| `:diffupdate` | Recompute diff |

If two files are already open in two windows:

```vim
:windo diffthis
```

Later:

```vim
:windo diffoff
```

## 16.3 Diff-mode navigation and merging

| Command | Meaning |
|---|---|
| `]c` | Next diff hunk |
| `[c` | Previous diff hunk |
| `do` | Obtain hunk from other window (`:diffget`) |
| `dp` | Put hunk to other window (`:diffput`) |
| `:diffget` | Pull changes from another diff window |
| `:diffput` | Push changes to another diff window |

### Typical merge workflow

1. Open files in diff mode
2. Use `[c` and `]c` to move between hunks
3. Use `do` / `:diffget` to take changes
4. Use `dp` / `:diffput` to send changes
5. Save merged file

---

## 17. External commands, filters, and shell integration

## 17.1 Run shell commands

| Command | Meaning |
|---|---|
| `:!cmd` | Run shell command |
| `:read !cmd` or `:r !cmd` | Insert command output below cursor |
| `:%!cmd` | Filter whole buffer through command |

### Examples

```vim
:!ls -lah
:r !date
:%!sort
```

## 17.2 Filter part of a buffer through a command

The `!` operator works with motions and text objects.

Example:

```text
!ip sort
```

Meaning:

1. `!` enters filter-operator mode
2. `ip` selects inner paragraph
3. `sort` is run on that text

This is extremely powerful for processing text with Unix tools.

## 17.3 Save a root-owned file via `sudo`

If a file was opened without permissions:

```vim
:write !sudo tee % >/dev/null
```

This writes the buffer to the current file path (`%`) using `sudo tee`.

> [!warning]
> This writes the file contents successfully, but the current buffer may still not become “owned” by root in an interactive editing sense. It is a rescue technique, not a substitute for opening files appropriately.

## 17.4 Save a Visual selection to a file

1. Select text in Visual mode
2. Press `:`
3. Run:

```vim
:'<,'>write new_file.txt
```

This writes only the selected lines to a new file.

---

## 18. Built-in terminal, command-line window, completion, help

## 18.1 Terminal buffers

Open terminal:

```vim
:terminal
```

Useful split forms:

```vim
:botright split | terminal
:vert terminal
```

### Terminal-mode essentials

| Command | Meaning |
|---|---|
| `<C-\><C-n>` | Leave Terminal mode and enter Terminal-Normal mode |
| `i` | Return from Terminal-Normal mode to Terminal job input |
| `<C-w>...` | Window commands after leaving Terminal mode |

## 18.2 Insert-mode completion

| Command | Meaning |
|---|---|
| `<C-n>` | Next completion match |
| `<C-p>` | Previous completion match |
| `<C-x><C-f>` | File name completion |
| `<C-x><C-l>` | Whole line completion |
| `<C-r>{register}` | Insert register contents |

### Very useful insert-mode editing keys

| Command | Meaning |
|---|---|
| `<C-w>` | Delete previous word |
| `<C-u>` | Delete back to start of insert |
| `<C-o>` | Execute one Normal-mode command, then return to Insert |

## 18.3 Command-line window

For complex `:` commands or searches:

| Command | Meaning |
|---|---|
| `q:` | Open command-line history window |
| `q/` | Open search history window |
| `<C-f>` | From command-line mode, open command-line window |

## 18.4 Help system

| Command | Meaning |
|---|---|
| `:help` | Open help |
| `:help topic` | Help for a topic |
| `:help ctrl-v` | Help for blockwise visual mode |
| `:help cgn` | Help for `cgn` |
| `:help registers` | Help for registers |
| `:help quickfix` | Help for quickfix |
| `:helpgrep pattern` | Search help files |

### High-value help topics

```vim
:help motion.txt
:help text-objects
:help visual-block
:help registers
:help quote_
:help quickfix
:help diff
```

---

## 19. High-yield recipes

## 19.1 Rename a word one match at a time

```text
*           search current word
cgnNEW<Esc>
. . .       repeat
```

Best when not every match should change.

## 19.2 Replace all exact matches in file with confirmation

```vim
:%s/\<old_name\>/new_name/gc
```

## 19.3 Prefix many lines

Use linewise substitute:

```vim
:%s/^/# /
```

Or blockwise insert:

1. `<C-v>` select first column across lines
2. `I# <Esc>`

## 19.4 Append text to many lines

```vim
:%s/$/;/
```

Or blockwise append with `<C-v>` + `A;<Esc>`.

## 19.5 Reindent entire file

```vim
gg=G
```

## 19.6 Compare two files

```bash
nvim -d old.conf new.conf
```

Then inside Neovim:

- `[c` / `]c` to move between changes
- `do` to take from other side
- `dp` to send to other side

## 19.7 Run a macro on selected lines

1. Record macro in register `a`
2. Select lines with `V`
3. Run:

```vim
:'<,'>normal! @a
```

## 19.8 Toggle between current and previous buffer

```text
<C-^>
```

or:

```vim
:b#
```

## 19.9 Open file under cursor

```text
gf
gF
<C-w>f
<C-w>F
```

## 19.10 Replace selected text but keep original yank

In Visual mode:

```vim
"_dP
```

---

## 20. Common pitfalls and corrected myths

> [!warning]
> These are frequent misunderstandings.

### `.` does not replay macros

- **Correct:** `.` repeats the **last change**
- **Incorrect:** `.` repeats the last macro

Use `@a`, `@@`, or `{count}@a` for macros.

### `%` does not mean “inside delimiters”

- **Correct:** `%` jumps to the matching delimiter/item
- **Incorrect:** `d%` means “delete inside parentheses”

Use:

- `di(` / `da(` for parentheses
- `di{` / `da{` for braces
- `di[` / `da[` for brackets

### Tabs are not files

- **Buffer** = file/text in memory
- **Window** = view onto a buffer
- **Tab** = layout of windows

### There is no built-in VS Code-style multi-cursor

Stock Neovim’s main answers are:

- **blockwise Visual mode** (`<C-v>`)
- **substitute** (`:%s/.../.../gc`)
- **`cgn` + `.`**
- **macros**
- **quickfix + `:cfdo`**
- **`:global`**

### `U` is not redo

- Use `u` to undo
- Use `<C-r>` to redo
- Treat `U` as a niche line-undo command

---

## 21. What to memorize first

> [!tip]
> The fastest path to proficiency is to internalize these first.

### First wave

```text
<Esc>
i a o O
h j k l
w b e
0 ^ $
gg G
x dd yy p
u <C-r>
/ ? n N
:v / V / <C-v>
: w q x
```

### Second wave

```text
diw ciw daw
ci" ci( di{
f t ; ,
* #
.
gv
<C-o> <C-i>
```

### Third wave

```text
cgn
qa ... q
@a @@
:ls
:b#
<C-w>h/j/k/l
gf gF
:vimgrep
:copen
nvim -d
```

---

## 22. Deliberate practice order

1. **Normal mode movement**
   - `hjkl`, `wbe`, `0^$`, `gg/G`, `f/t`
2. **Editing grammar**
   - `dw`, `cw`, `dd`, `yy`, `p`, `u`, `.`
3. **Text objects**
   - `ciw`, `diw`, `ci"`, `da(`, `vap`
4. **Visual and blockwise editing**
   - `v`, `V`, `<C-v>`, `I`, `A`, `c`, `gv`
5. **Search and replace**
   - `/`, `*`, `:%s/.../.../gc`, `cgn`
6. **Macros**
   - `qa`, `@a`, `@@`, `:normal! @a`
7. **Navigation state**
   - marks, jumplist, changelist
8. **Workspace management**
   - buffers, splits, tabs, `gf`
9. **Project workflows**
   - tags, quickfix, `:vimgrep`, `:argdo`, `:cfdo`
10. **Diff and shell integration**
    - `nvim -d`, `do`, `dp`, `:%!cmd`

---

## 23. One-screen cheat sheet

```text
INSERT
  i a I A o O gi

MOVE
  h j k l
  w b e / W B E
  0 ^ $ g_
  f F t T ; ,
  gg G {n}G
  % ( ) { }
  H M L
  <C-d> <C-u> <C-f> <C-b>
  zz zt zb

EDIT
  x X dd D
  cc C s S r R
  yy Y p P
  u <C-r> .
  J gJ
  >> << == gg=G
  gu gU g~
  <C-a> <C-x>

TEXT OBJECTS
  iw aw
  ip ap
  i" a"
  i' a'
  i( a(
  i[ a[
  i{ a{

VISUAL
  v V <C-v>
  o O gv
  I A c d y r

SEARCH / REPLACE
  / ? n N
  * # g* g#
  :noh
  :%s/old/new/g
  :%s/\<word\>/new/gc
  cgn then .

REGISTERS / MACROS
  :registers
  "ayy  "ap  "+yy
  "_d
  qa ... q
  @a  @@  10@a

JUMPS / MARKS
  ma  'a  `a
  <C-o> <C-i>
  g; g,
  :marks :jumps :changes

BUFFERS / WINDOWS / TABS
  :ls  :b#  <C-^>
  :bn  :bp  :bd
  :sp  :vs
  <C-w>h/j/k/l
  :tabnew  gt  gT

FILES / PROJECTS
  gf  gF  <C-w>f
  :find
  ctags -R .
  <C-]>  <C-t>
  :vimgrep /pat/gj **/*
  :copen  :cn  :cp
  :argdo
  :cfdo

DIFF
  nvim -d a b
  :diffsplit file
  [c ]c
  do dp
  :diffoff!
```

--- 

---

# Neovim — Permanent Reference (Part 2: Advanced Workflows, Persistence, and Customization)

> [!note]
> **Scope:** advanced built-in Neovim features that complement Part 1: startup flags, Ex ranges, command-line mode, advanced regex/substitute, batch execution, persistence, recovery, folds, spelling, mappings, autocmds, and troubleshooting.  
> All commands are valid for stock Neovim behavior as of **March 2026**.

> [!important]
> Part 1 covered the core editing model.  
> Part 2 covers the features that make Neovim a durable long-term tool:
>
> - launching Neovim in different modes
> - addressing text precisely with **ranges**
> - batch editing many files safely
> - persistent undo, swap, sessions, recovery
> - advanced command-line usage and customization
> - diagnostics and environment-specific caveats on Arch Linux / Wayland

---

## 1. Startup, invocation, and safe launch modes

## 1.1 Common startup forms

```bash
nvim
nvim file.txt
nvim file1 file2 file3
nvim + file.txt
nvim +42 file.txt
nvim +'set number' file.txt
nvim +'norm! gg=G' file.txt
nvim -d file1 file2
nvim -o file1 file2
nvim -O file1 file2
nvim -p file1 file2
```

### Meaning

| Command | Meaning |
|---|---|
| `nvim` | Start with an empty buffer |
| `nvim file` | Open a file |
| `nvim + file` | Open file and jump to last line |
| `nvim +42 file` | Open file at line 42 |
| `nvim +'cmd' file` | Run an Ex command on startup |
| `nvim -d a b` | Open files in diff mode |
| `nvim -o a b` | Horizontal splits |
| `nvim -O a b` | Vertical splits |
| `nvim -p a b` | Tabs |

## 1.2 Clean and troubleshooting launches

These are essential when debugging configuration or reproducing stock behavior.

```bash
nvim --clean
nvim -u NONE -i NONE
nvim -u NORC -i NONE
nvim -n file.txt
nvim -R file.txt
nvim -M file.txt
nvim --headless +'q'
```

### Meaning

| Command | Meaning |
|---|---|
| `nvim --clean` | Start with a clean default environment, bypassing user config |
| `nvim -u NONE -i NONE` | No init file and no ShaDa file |
| `nvim -u NORC -i NONE` | Skip user config and ShaDa while keeping runtime defaults |
| `nvim -n file` | Disable swap file for this session |
| `nvim -R file` | Read-only mode |
| `nvim -M file` | Non-modifiable mode |
| `nvim --headless` | Run without opening the UI |

> [!tip]
> For configuration debugging, `nvim --clean` is usually the first command to try.

## 1.3 Reading from standard input

```bash
printf 'hello\nworld\n' | nvim -
rg --files | nvim -
```

- `nvim -` opens a buffer from standard input.
- That buffer does not have a normal on-disk file path unless you later `:write` it.

---

## 2. Command-line mode and Ex command fundamentals

## 2.1 Entering command-line mode

| Command | Meaning |
|---|---|
| `:` | Ex command-line |
| `/` | Forward search |
| `?` | Backward search |

## 2.2 Command-line editing keys

While typing `:`, `/`, or `?` commands:

| Key | Meaning |
|---|---|
| `<C-b>` | Move to beginning of command line |
| `<C-e>` | Move to end of command line |
| `<Left>` / `<Right>` | Move cursor |
| `<C-w>` | Delete previous word |
| `<C-u>` | Delete to start of command line |
| `<Up>` / `<Down>` | Browse command history |
| `<C-p>` / `<C-n>` | Previous / next history entry |
| `<C-r><C-w>` | Insert word under cursor |
| `<C-r><C-a>` | Insert WORD under cursor |
| `<C-r>"` | Insert unnamed register |
| `<C-f>` | Open command-line window |

### Command-line window

| Command | Meaning |
|---|---|
| `q:` | Open command history window |
| `q/` | Open search history window |
| `q?` | Open backward-search history window |

> [!note]
> The command-line window is ideal for editing long substitutions, `:global` commands, or complex regex patterns.

---

## 3. Ex ranges: addressing exactly the text you want

Ranges are one of the most important advanced Neovim features.

## 3.1 Common addresses

| Address | Meaning |
|---|---|
| `.` | Current line |
| `$` | Last line |
| `%` | Whole file, equivalent to `1,$` |
| `1` | First line |
| `'<` | Start of last Visual selection |
| `'>` | End of last Visual selection |
| `'a` | Line of mark `a` |
| `/pattern/` | Next line matching pattern |
| `?pattern?` | Previous line matching pattern |

## 3.2 Relative addresses

| Form | Meaning |
|---|---|
| `.+1` | One line below current line |
| `.-2` | Two lines above current line |
| `$-3` | Three lines before end of file |
| `'<+1,'>-1` | Inside a Visual line range, excluding first/last line |

## 3.3 `,` vs `;` in ranges

Both separate two addresses, but they do not behave identically.

- `addr1,addr2` evaluates `addr2` relative to the **original current line**
- `addr1;addr2` evaluates `addr2` relative to **addr1**

### Example

```vim
:/BEGIN/;/END/p
```

This prints from the next `BEGIN` to the next `END` **after that BEGIN**, not after the original cursor line.

## 3.4 High-value range examples

```vim
:.,$s/foo/bar/g
:1,10d
:'<,'>sort
:/BEGIN/,/END/write block.txt
:'a,'bs/^/# /
```

### Meaning

- `:.,$s/foo/bar/g` → substitute from current line to end of file
- `:1,10d` → delete lines 1 through 10
- `:'<,'>sort` → sort the selected lines
- `:/BEGIN/,/END/write block.txt` → write a delimited block to a file
- `:'a,'bs/^/# /` → prefix lines between marks `a` and `b`

---

## 4. Command modifiers: run commands more safely and precisely

Modifiers change how an Ex command behaves.

## 4.1 High-value command modifiers

| Modifier | Meaning |
|---|---|
| `silent` | Suppress normal command output |
| `confirm` | Ask for confirmation where applicable |
| `keepjumps` | Do not pollute the jumplist |
| `keeppatterns` | Do not replace the last search pattern |
| `keepalt` | Preserve alternate file |
| `lockmarks` | Preserve marks where possible |
| `noautocmd` | Do not trigger autocommands |
| `vertical` | Force vertical split form |
| `tab` | Open command result in a new tab |

## 4.2 Examples

```vim
:keepjumps keeppatterns %s/foo/bar/g
:silent write
:confirm quit
:vertical help quickfix
:tab split README.md
:noautocmd write
```

> [!tip]
> For scripted batch edits, `keepjumps`, `keeppatterns`, and `normal!` are especially valuable.

---

## 5. Advanced search and regex

## 5.1 Regex “magic” modes

Neovim uses Vim regex syntax, not PCRE.

| Atom | Meaning |
|---|---|
| `\v` | Very magic: most punctuation becomes special |
| `\m` | Magic |
| `\M` | Nomagic |
| `\V` | Very nomagic: most characters are literal |

### Example

```vim
/\v(foo|bar)\d+
/\V/path/with/slashes
```

## 5.2 Useful regex atoms

| Atom | Meaning |
|---|---|
| `.` | Any character |
| `*` | Zero or more of previous atom |
| `\+` | One or more |
| `\=` | Zero or one |
| `\|` | Alternation |
| `\(` `\)` | Capture group |
| `^` | Start of line |
| `$` | End of line |
| `\<` | Start of word |
| `\>` | End of word |
| `\d` | Digit |
| `\x` | Hex digit |
| `\s` | Whitespace |
| `\k` | Keyword character |
| `\zs` | Start match here |
| `\ze` | End match here |
| `\_.` | Any character including newline |
| `.\{-}` | Non-greedy match |

## 5.3 Practical regex examples

### Match whole words only

```vim
/\<count\>
```

### Match text inside brackets without including brackets

```vim
/\[\zs.\{-}\ze\]
```

### Match across lines

```vim
/\vBEGIN\_.{-}END
```

### Case control inside a pattern

```vim
/\cerror
/\CError
```

---

## 6. Advanced substitute techniques

## 6.1 Replacement-side special forms

Inside the replacement part of `:s`:

| Form | Meaning |
|---|---|
| `&` | Whole matched text |
| `\0` | Whole matched text |
| `\1` `\2` ... | Capture groups |
| `\r` | Line break in replacement |
| `\=` | Evaluate a Vimscript expression |

> [!warning]
> In a replacement, `\r` inserts a newline.  
> Do not confuse this with search-side regex behavior.

## 6.2 Capture-group examples

### Swap `last, first` to `first last`

```vim
:%s/\v(\w+),\s+(\w+)/\2 \1/g
```

### Wrap every word `foo` in brackets

```vim
:%s/\<foo\>/[&]/g
```

### Split comma-separated items onto new lines

```vim
:%s/,\s*/\r/g
```

## 6.3 Expression substitutions

### Lowercase the current word matches dynamically

```vim
:%s/\<\w\+\>/\=tolower(submatch(0))/g
```

### Number lines in a selected range

```vim
:let i = 1
:'<,'>s/^/\=printf('%d. ', i)/ | let i += 1
```

### Trim trailing whitespace

```vim
:%s/\s\+$//e
```

## 6.4 Reusing the last replacement

| Command | Meaning |
|---|---|
| `:&` | Repeat last `:substitute` on current line |
| `:&&` | Repeat last `:substitute` with same flags |
| `g&` | Repeat last substitute on all lines |

---

## 7. `:global`, `:vglobal`, and structural editing

These are among the most powerful batch-editing tools in Vim/Neovim.

## 7.1 Core forms

| Command | Meaning |
|---|---|
| `:g/pat/cmd` | Run `cmd` on lines matching `pat` |
| `:v/pat/cmd` | Run `cmd` on lines not matching `pat` |

## 7.2 High-value examples

```vim
:g/ERROR/d
:v/^\s*#/d
:g/^\s*$/d
:g/TODO/normal! I- 
:g/^\s*$/,/./-1join
```

### Meaning

- `:g/ERROR/d` → delete lines containing `ERROR`
- `:v/^\s*#/d` → delete lines that are not comments
- `:g/^\s*$/d` → delete blank lines
- `:g/TODO/normal! I- ` → prefix matching lines
- `:g/^\s*$/,/./-1join` → join paragraph blocks separated by blank lines

> [!important]
> `:global` selects **lines**, then executes an Ex command for each selected line.  
> It is line-oriented, not a general arbitrary-region engine.

---

## 8. Batch execution across buffers, files, windows, tabs, and lists

## 8.1 Batch command families

| Command | Meaning |
|---|---|
| `:argdo` | Run a command for each file in the argument list |
| `:bufdo` | Run a command for each buffer |
| `:windo` | Run a command in each window |
| `:tabdo` | Run a command in each tab |
| `:cdo` | Run a command for each quickfix entry |
| `:cfdo` | Run a command once per quickfix file |
| `:ldo` | Run a command for each location-list entry |
| `:lfdo` | Run a command once per location-list file |

## 8.2 Examples

### Replace across the argument list

```vim
:args **/*.lua
:argdo %s/\<foo\>/bar/ge | update
```

### Save all listed buffers

```vim
:bufdo update
```

### Enable numbers in every window

```vim
:windo set number relativenumber
```

### Close all other tabs’ folds or reapply options

```vim
:tabdo setlocal cursorline
```

### Quickfix-based project replacement

```vim
:vimgrep /\<old_name\>/gj **/*.py
:copen
:cfdo %s/\<old_name\>/new_name/ge | update
```

> [!tip]
> Prefer `:cfdo` over `:cdo` for file-wide substitutions.  
> `:cdo` runs once per quickfix **entry**, which is often wasteful for substitutions.

---

## 9. Argument list, working directory, and file set management

## 9.1 Argument-list commands

| Command | Meaning |
|---|---|
| `:args` | Show current argument list |
| `:args **/*.c` | Set the argument list |
| `:next` / `:n` | Next file in argument list |
| `:previous` / `:prev` | Previous file |
| `:first` / `:last` | First / last file |
| `:argadd file` | Add file to argument list |
| `:argdelete file` | Remove file from argument list |
| `:all` | Open all argument-list files in windows |

## 9.2 Working directory control

| Command | Meaning |
|---|---|
| `:pwd` | Show working directory |
| `:cd dir` | Change global working directory |
| `:lcd dir` | Change window-local working directory |
| `:tcd dir` | Change tab-local working directory |

> [!note]
> `:lcd` is especially useful when different windows should operate in different project roots.

---

## 10. Persistent undo, ShaDa, swap, backup, and recovery

## 10.1 Persistent undo

Persistent undo survives editor restarts.

### Recommended setup

```bash
install -d -m 700 ~/.local/state/nvim/{undo,swap,backup}
```

```lua
local state = vim.fn.stdpath("state")

vim.opt.undofile = true
vim.opt.undodir = state .. "/undo"
vim.opt.directory = state .. "/swap//"
vim.opt.backupdir = state .. "/backup//"
vim.opt.writebackup = true
```

> [!note]
> A trailing `//` in `directory` or `backupdir` tells Vim/Neovim to use a full-path-derived filename, reducing collisions between files with the same basename in different directories.

## 10.2 Undo history navigation

| Command | Meaning |
|---|---|
| `u` | Undo |
| `<C-r>` | Redo |
| `g-` | Older text state |
| `g+` | Newer text state |
| `:earlier 5m` | Go to state from 5 minutes earlier |
| `:later 5m` | Go forward 5 minutes |
| `:undolist` | Show undo branches |

> [!important]
> Undo history is a tree, not a simple linear stack.  
> `g-`, `g+`, and `:undolist` become much more useful once `undofile` is enabled.

## 10.3 ShaDa

Neovim uses **ShaDa** for persistent history, marks, registers, and more.

| Command | Meaning |
|---|---|
| `:wshada` | Write ShaDa file now |
| `:rshada` | Read ShaDa file now |
| `:oldfiles` | List recently edited files |

## 10.4 Swap files and recovery

| Command | Meaning |
|---|---|
| `:preserve` | Write out swap data immediately |
| `:swapname` | Show current swap file path |
| `nvim -r` | List recoverable swap files |
| `nvim -r file.txt` | Recover a specific file |
| `:recover` | Recover current file from swap |

> [!warning]
> `-n` disables swap files.  
> Use it only intentionally, because it reduces crash recovery capability.

## 10.5 Sessions and views

### Sessions

Sessions preserve the layout of windows, tabs, and buffers.

| Command | Meaning |
|---|---|
| `:mksession Session.vim` | Write a session file |
| `:source Session.vim` | Restore that session |
| `nvim -S Session.vim` | Start Neovim and load session |

### Views

Views preserve per-window state such as folds and cursor position.

| Command | Meaning |
|---|---|
| `:mkview` | Save a view for current file/window |
| `:loadview` | Restore saved view |

---

## 11. Folds

Folds let you collapse text structurally or manually.

## 11.1 Fold commands

| Command | Meaning |
|---|---|
| `za` | Toggle fold under cursor |
| `zo` | Open fold |
| `zc` | Close fold |
| `zO` | Open fold recursively |
| `zC` | Close fold recursively |
| `zR` | Open all folds |
| `zM` | Close all folds |
| `zr` | Reduce fold level restrictions |
| `zm` | Increase folding |
| `zi` | Toggle `foldenable` |
| `zd` | Delete fold under cursor |
| `zD` | Delete fold recursively |
| `zf{motion}` | Create manual fold |
| `zfj` | Fold current and next line |
| `zfa}` | Fold “around paragraph/block” via motion |

## 11.2 Fold methods

| Option | Meaning |
|---|---|
| `foldmethod=manual` | Manual folds only |
| `foldmethod=indent` | Fold by indentation |
| `foldmethod=marker` | Fold by markers such as `{{{` `}}}` |
| `foldmethod=syntax` | Fold by syntax definitions |
| `foldmethod=expr` | Fold by custom expression |

### Common practical setup

```lua
vim.opt.foldmethod = "indent"
vim.opt.foldlevelstart = 99
```

This keeps indent-based folding available without opening files fully folded.

---

## 12. Spelling, whitespace, line endings, and encodings

## 12.1 Spell checking

### Enable spell checking

```vim
:set spell
:set spelllang=en_us
```

### Spell commands

| Command | Meaning |
|---|---|
| `]s` | Next misspelled word |
| `[s` | Previous misspelled word |
| `z=` | Suggestions for word under cursor |
| `zg` | Add word as good |
| `zw` | Mark word as wrong |
| `zug` | Undo `zg` |
| `zuw` | Undo `zw` |

## 12.2 Visible whitespace

### Recommended inspection settings

```lua
vim.opt.list = true
vim.opt.listchars = {
  tab = "»·",
  trail = "·",
  nbsp = "␣",
  extends = "⟩",
  precedes = "⟨",
}
```

### Commands

| Command | Meaning |
|---|---|
| `:set list` | Show whitespace markers |
| `:set nolist` | Hide them |
| `:set listchars?` | Inspect `listchars` |

## 12.3 Line endings and encodings

| Command | Meaning |
|---|---|
| `:set fileformat?` | Show line-ending format |
| `:set fileformat=unix` | Use LF |
| `:set fileformat=dos` | Use CRLF |
| `:set fileencoding?` | Show file encoding |
| `:set fileencoding=utf-8` | Use UTF-8 |
| `:set bomb?` | Check for UTF-8 BOM |
| `:set nobomb` | Disable BOM on write |

> [!tip]
> If a file looks “corrupted” or has odd characters, check `fileencoding`, `fileformat`, and visible whitespace before assuming the file content itself is wrong.

---

## 13. Formatting and filetype-aware behavior

## 13.1 Filetype, indent, and syntax status

| Command | Meaning |
|---|---|
| `:filetype` | Show filetype detection status |
| `:set filetype?` | Show current filetype |
| `:syntax` | Show syntax status |
| `:set syntax?` | Show syntax option |
| `:setlocal shiftwidth? tabstop? expandtab?` | Inspect indent options |

## 13.2 Common formatting-related options

```lua
vim.opt.expandtab = true
vim.opt.tabstop = 4
vim.opt.shiftwidth = 4
vim.opt.softtabstop = 4
vim.opt.autoindent = true
vim.opt.smartindent = false
```

> [!note]
> `smartindent` is a legacy heuristic and is often less predictable than filetype-based indentation.  
> In modern setups, filetype-specific indent logic is usually preferable.

## 13.3 Paste mode

When pasting badly indented text into a terminal Neovim session:

```vim
:set paste
:set nopaste
```

> [!warning]
> `paste` disables several insert-mode conveniences.  
> Always turn it off again with `:set nopaste`.

---

## 14. Mappings and abbreviations

## 14.1 Mapping families

| Command | Scope |
|---|---|
| `:map` | Normal, Visual, Select, Operator-pending |
| `:nmap` | Normal only |
| `:vmap` | Visual and Select |
| `:xmap` | Visual only |
| `:smap` | Select only |
| `:omap` | Operator-pending only |
| `:imap` | Insert only |
| `:cmap` | Command-line only |
| `:tmap` | Terminal only |

### Non-recursive forms

| Command | Scope |
|---|---|
| `:noremap` | General non-recursive |
| `:nnoremap` | Normal non-recursive |
| `:vnoremap` | Visual non-recursive |
| `:inoremap` | Insert non-recursive |
| `:cnoremap` | Command-line non-recursive |
| `:tnoremap` | Terminal non-recursive |

> [!important]
> For user mappings, prefer **non-recursive** mappings unless recursive expansion is explicitly needed.

## 14.2 Inspect and remove mappings

| Command | Meaning |
|---|---|
| `:map` | Show mappings |
| `:nmap` | Show Normal mappings |
| `:verbose map <lhs>` | Show mapping source |
| `:unmap <lhs>` | Remove mapping |
| `:nunmap <lhs>` | Remove Normal mapping |

## 14.3 Recommended Lua mapping form

```lua
vim.keymap.set("n", "<Esc>", "<cmd>nohlsearch<CR>", { desc = "Clear search highlight" })
vim.keymap.set("n", "<leader>w", "<cmd>write<CR>", { desc = "Save file" })
vim.keymap.set("v", "<", "<gv", { desc = "Outdent and reselect" })
vim.keymap.set("v", ">", ">gv", { desc = "Indent and reselect" })
```

### Useful options

| Option | Meaning |
|---|---|
| `desc` | Description |
| `silent` | Suppress command echo |
| `expr` | Evaluate RHS as expression |
| `buffer` | Buffer-local mapping |
| `remap` | Allow recursive behavior |

## 14.4 Abbreviations

| Command | Meaning |
|---|---|
| `:iabbrev teh the` | Insert-mode abbreviation |
| `:cabbrev W w` | Command-line abbreviation |
| `:abbrev` | Show abbreviations |
| `:iunabbrev teh` | Remove insert abbreviation |

> [!warning]
> `:cabbrev` is powerful but easy to overuse.  
> Poor command-line abbreviations can interfere with normal command entry.

---

## 15. Autocommands and user commands

## 15.1 Lua autocommands

```lua
local group = vim.api.nvim_create_augroup("my_settings", { clear = true })

vim.api.nvim_create_autocmd("TextYankPost", {
  group = group,
  callback = function()
    vim.highlight.on_yank({ timeout = 150 })
  end,
})

vim.api.nvim_create_autocmd("FileType", {
  group = group,
  pattern = { "markdown", "text" },
  callback = function(args)
    vim.bo[args.buf].spell = true
    vim.bo[args.buf].textwidth = 80
  end,
})
```

## 15.2 User commands

```lua
vim.api.nvim_create_user_command("TrimTrailingWhitespace", function()
  local view = vim.fn.winsaveview()
  vim.cmd([[%s/\s\+$//e]])
  vim.fn.winrestview(view)
end, { desc = "Remove trailing whitespace" })
```

### Benefits of the pattern above

- preserves cursor/view position
- suppresses “pattern not found” via `e`
- creates a reusable named command

## 15.3 Useful autocommand events

| Event | Meaning |
|---|---|
| `BufReadPost` | After reading a buffer |
| `BufWritePre` | Before writing a buffer |
| `BufWritePost` | After writing |
| `FileType` | Filetype detected |
| `TextYankPost` | After yanking |
| `TermOpen` | Terminal buffer opened |
| `VimEnter` | After startup |
| `LspAttach` | When an LSP client attaches |

> [!note]
> `LspAttach` is useful only if you are using Neovim’s built-in LSP or an LSP-configuring plugin.

---

## 16. Lua configuration essentials

## 16.1 Standard configuration paths on Linux

| Purpose | Typical path |
|---|---|
| Config | `~/.config/nvim/init.lua` |
| Data | `~/.local/share/nvim/` |
| State | `~/.local/state/nvim/` |
| Cache | `~/.cache/nvim/` |

### Inspect paths from inside Neovim

```lua
:lua print(vim.fn.stdpath("config"))
:lua print(vim.fn.stdpath("data"))
:lua print(vim.fn.stdpath("state"))
:lua print(vim.fn.stdpath("cache"))
```

## 16.2 Option scopes in Lua

```lua
vim.opt.number = true           -- convenient generic option API
vim.bo.expandtab = true         -- buffer-local
vim.wo.cursorline = true        -- window-local
```

## 16.3 Running Lua directly

| Command | Meaning |
|---|---|
| `:lua print("hello")` | Run Lua |
| `:luado expr` | Run Lua over each line in a range |
| `:luafile %` | Source current Lua file |

### Example: evaluate line-by-line

```vim
:%luado return string.upper(line)
```

> [!note]
> `:luado` is niche but useful for one-off structured transformations.

---

## 17. Built-in terminal and command execution, advanced usage

## 17.1 Terminal commands

| Command | Meaning |
|---|---|
| `:terminal` | Open terminal |
| `:vert terminal` | Vertical terminal split |
| `:botright split | terminal` | Terminal in bottom split |

## 17.2 Terminal-mode mapping example

```lua
vim.keymap.set("t", "<Esc>", [[<C-\><C-n>]], { desc = "Leave terminal mode" })
```

> [!warning]
> Mapping raw `<Esc>` in terminal mode can interfere with terminal applications that legitimately use Escape.  
> Many users prefer a less ambiguous mapping such as `<C-[>` or a leader-based key.

## 17.3 Running external commands from Lua

For new asynchronous shell integration, prefer `vim.system()`.

```lua
vim.system({ "rg", "--files" }, { text = true }, function(obj)
  if obj.code == 0 then
    vim.schedule(function()
      print(obj.stdout)
    end)
  end
end)
```

> [!note]
> `vim.system()` is the modern Lua API for spawning external commands.  
> It is generally preferable to older ad hoc job-control patterns for new Lua code.

---

## 18. Quickfix, location lists, and compiler workflows

## 18.1 Quickfix population sources

Common sources:

- `:grep`
- `:vimgrep`
- `:make`
- `:cexpr`
- LSP diagnostics or plugin integrations

## 18.2 `:make` with quickfix

```vim
:set makeprg=make
:make
:copen
```

If your project builds with `make`, this gives a built-in compile-and-jump workflow.

## 18.3 Manual quickfix population

```vim
:cexpr [{'filename': 'a.txt', 'lnum': 10, 'col': 3, 'text': 'example'}]
:copen
```

This is mainly useful from scripts or advanced automation.

---

## 19. Help, introspection, and troubleshooting

## 19.1 Core diagnostic commands

| Command | Meaning |
|---|---|
| `:checkhealth` | Run health checks |
| `:messages` | Show message history |
| `:scriptnames` | List sourced scripts |
| `:verbose set option?` | Show option value and where last set |
| `:verbose map <lhs>` | Show mapping source |
| `:version` | Version and feature info |
| `:echo executable('wl-copy')` | Check external executable presence |

## 19.2 Startup logging

```bash
nvim -V3nvim-startup.log
```

This writes verbose startup logging to `nvim-startup.log`.

## 19.3 Performance and config debugging workflow

1. `nvim --clean`
2. If issue disappears, config is involved
3. Use:
   - `:scriptnames`
   - `:verbose set option?`
   - `:verbose map key`
   - `:checkhealth`

## 19.4 File-local state inspection

| Command | Meaning |
|---|---|
| `:setlocal` | Show local options that differ |
| `:echo &filetype` | Show current filetype |
| `:echo expand('%:p')` | Absolute file path |
| `:file` | Show buffer name and status |

---

## 20. Arch Linux / Wayland specifics

## 20.1 Clipboard integration

Recommended packages:

```bash
sudo pacman -S neovim wl-clipboard xclip xsel
```

> [!note]
> On pure Wayland systems, `wl-clipboard` is the primary clipboard provider.  
> `xclip` and `xsel` are mainly relevant for X11 or Xwayland compatibility cases.

### Verify clipboard provider health

```vim
:checkhealth
```

Look for the clipboard section.

### Recommended option

```lua
vim.opt.clipboard = "unnamedplus"
```

This makes the default yank/delete/paste operations use the `"+` clipboard register.

## 20.2 Common clipboard tests

### Yank a line to the system clipboard

```vim
"+yy
```

### Paste from the system clipboard

```vim
"+p
```

### Shell-side test under Wayland

```bash
printf 'hello\n' | wl-copy
wl-paste
```

## 20.3 Environment caveats

Clipboard behavior depends on:

- whether Neovim can find `wl-copy` / `wl-paste`
- terminal support
- whether the process is running in a valid graphical session
- sandboxing/containerization if applicable

> [!warning]
> If clipboard access fails inside a container, SSH session, or restricted service environment, the problem may be the session environment rather than Neovim itself.

---

## 21. Advanced recipes

## 21.1 Delete duplicate blank lines

```vim
:%s/\(\n\s*\)\{3,}/\r\r/g
```

Practical alternative:

```vim
:g/^\s*$/,/./-1join
```

## 21.2 Sort a selected block uniquely

Select lines, then:

```vim
:'<,'>sort u
```

## 21.3 Keep only matching lines

```vim
:v/pattern/d
```

## 21.4 Keep only non-comment, non-blank lines

```vim
:g/^\s*#/d | g/^\s*$/d
```

or invert-first approach:

```vim
:v/^\s*#/d
:g/^\s*$/d
```

## 21.5 Prefix a numeric sequence down a block

1. Make a block selection over existing numbers or create one
2. Use:

```text
g<C-a>
```

This increments sequentially across the selected lines.

## 21.6 Save and restore a repeatable workspace

```vim
:mksession Session.vim
```

Later:

```bash
nvim -S Session.vim
```

## 21.7 Recover from accidental large edits without closing Neovim

```vim
:undolist
g-
g+
:earlier 10m
:later 10m
```

## 21.8 Batch-trim trailing whitespace in a project

```vim
:args **/*
:argdo %s/\s\+$//e | update
```

> [!warning]
> Do not run the previous command blindly in repositories containing binaries, generated files, vendored code, or files where whitespace is semantically significant.

---

## 22. Recommended minimal advanced config

```lua
local state = vim.fn.stdpath("state")

vim.opt.ignorecase = true
vim.opt.smartcase = true
vim.opt.hlsearch = true
vim.opt.incsearch = true

vim.opt.undofile = true
vim.opt.undodir = state .. "/undo"
vim.opt.directory = state .. "/swap//"
vim.opt.backupdir = state .. "/backup//"
vim.opt.writebackup = true

vim.opt.list = true
vim.opt.listchars = {
  tab = "»·",
  trail = "·",
  nbsp = "␣",
}

vim.opt.foldmethod = "indent"
vim.opt.foldlevelstart = 99

vim.opt.clipboard = "unnamedplus"
vim.opt.path:append("**")
vim.opt.grepprg = "rg --vimgrep --smart-case"
vim.opt.grepformat = "%f:%l:%c:%m"

vim.keymap.set("n", "<Esc>", "<cmd>nohlsearch<CR>", { desc = "Clear search highlight" })
vim.keymap.set("v", "<", "<gv", { desc = "Outdent and reselect" })
vim.keymap.set("v", ">", ">gv", { desc = "Indent and reselect" })

local group = vim.api.nvim_create_augroup("core", { clear = true })
vim.api.nvim_create_autocmd("TextYankPost", {
  group = group,
  callback = function()
    vim.highlight.on_yank({ timeout = 150 })
  end,
})
```

### Create state directories once

```bash
install -d -m 700 ~/.local/state/nvim/{undo,swap,backup}
```

---

## 23. What Part 2 adds beyond Part 1

> [!tip]
> If Part 1 taught you how to **edit**, Part 2 teaches you how to **operate Neovim as a system**.

### Master these next

```text
RANGES
  .  $  %  '<,'>  'a,'b  /pat/;/pat2/

BATCH
  :argdo
  :bufdo
  :windo
  :tabdo
  :cfdo

PERSISTENCE
  undofile
  :undolist
  g- g+
  :mksession
  :mkview
  :recover

ADVANCED TEXT
  \v \V
  \zs \ze
  \_.  .\{-}
  :g / :v
  :s with \1, &, \=expr

CUSTOMIZATION
  vim.keymap.set()
  nvim_create_autocmd()
  nvim_create_user_command()

TROUBLESHOOTING
  nvim --clean
  :checkhealth
  :scriptnames
  :verbose set option?
  :verbose map key
```

---

## 24. Final memorization shortlist for advanced daily use

```text
STARTUP
  nvim --clean
  nvim -d a b
  nvim -S Session.vim

RANGES / COMMANDS
  :.,$s/foo/bar/g
  :'<,'>sort
  :g/pat/d
  :v/pat/d
  :keepjumps keeppatterns %s/foo/bar/g

BATCH
  :args **/*.ext
  :argdo cmd | update
  :cfdo cmd | update
  :windo set number
  :tabdo setlocal cursorline

PERSISTENCE
  :undolist
  g- g+
  :earlier 5m
  :mksession
  :mkview
  :recover

FOLDS / SPELL
  za zo zc zR zM
  :set spell
  ]s [s z=

INSPECT / DEBUG
  :checkhealth
  :messages
  :scriptnames
  :verbose set option?
  :verbose map lhs
```

--- 

