# 󰏖 Dusky Git Time Machine

A fast, interactive, and crash-proof Git time-travel TUI designed specifically for dotfiles and bare repositories.

---

## 🗺️ How It Works (Visual Flow)

```text
  ┌──────────────────────────────────────────────────────────┐
  │                    PRESENT TIMELINE                      │
  │  • Active branch (e.g. main)                             │
  │  • Working directory has in-progress / uncommitted edits │
  └────────────────────────────┬─────────────────────────────┘
                               │
                [ENTER] Travel to Past Commit
                               │
                               ▼
  ┌──────────────────────────────────────────────────────────┐
  │                  STASH-SHIELD ACTIVATED                  │
  │  1. Uncommitted tracked work is saved to an isolated     │
  │     session stash (DUSKY_AUTO_STASH_<id>).               │
  │  2. Untracked files in $HOME are NEVER touched or lost.  │
  │  3. HEAD safely detaches onto historical commit.         │
  └────────────────────────────┬─────────────────────────────┘
                               │
                               ▼
  ┌──────────────────────────────────────────────────────────┐
  │                    IN THE PAST (VIEW)                    │
  │  • Files reflect the EXACT historical snapshot.          │
  │  • In-progress work stays sleeping in stash ledger.      │
  │  • Side-by-side Delta diff & commit tree inspection.     │
  └─────────────┬──────────────────────────────┬─────────────┘
                │                              │
        [CTRL-R], [ESC], or [CTRL-C]     [ALT-S] Stay Mode + Exit
                │                              │
                ▼                              ▼
  ┌───────────────────────────┐  ┌───────────────────────────┐
  │      AUTO-RETURN HOME     │  │       STAY DETACHED       │
  │ 1. Switches back to main. │  │ Leaves you in the past to │
  │ 2. Pops & restores your   │  │ compile, test, or debug.  │
  │    uncommitted work.      │  │                           │
  │ 3. Working state clean.   │  │ Return later via Ctrl-R   │
  │                           │  │ or Alt-O in the TUI.      │
  └───────────────────────────┘  └───────────────────────────┘
```

---

## ⌨️ Keyboard Shortcuts

### 🚀 Travel & Navigation
| Key | Action |
| :--- | :--- |
| **`Enter`** / **Double-Click** | Travel to the selected commit (stash shield activates automatically). |
| **`Ctrl-R`** | Return to the present timeline (`main`) and pop session stash. |
| **`Ctrl-G`** | Jump selection cursor directly to live HEAD. |
| **`Alt-A`** | Toggle scope: **All refs** (branches + tags + remotes) $\longleftrightarrow$ **Current lineage**. |
| **`Ctrl-L`** | Force refresh / reload commit graph. |

### 🧭 Vim Navigation Mode
| Key | Action |
| :--- | :--- |
| **`Alt-M`** | Toggle Vim navigation mode on / off. |
| **`j`** / **`k`** | Move cursor Down / Up. |
| **`g`** / **`G`** | Jump to First / Last commit in list. |
| **`Ctrl-D`** / **`Ctrl-U`** | Half-page down / up. |
| **`/`** | Enter search query mode. |
| **`Esc`** | Exit search back to Vim mode, or exit Time Machine and return home. |

### 🖼️ Inspect & Preview Layout
| Key | Action |
| :--- | :--- |
| **`Alt-P`** | Cycle preview mode: **Side-by-side** $\rightarrow$ **Inline** $\rightarrow$ **Stat** $\rightarrow$ **Files** $\rightarrow$ **vs Present**. |
| **`Alt-Left`** / **`Alt-Right`** | Dynamically resize horizontal preview width ($\pm 5\%$). |
| **`Alt-Up`** / **`Alt-Down`** | Dynamically resize vertical preview height ($\pm 5\%$). |
| **`Alt-H`** / **`Alt-J`** / **`Alt-K`** / **`Alt-L`** | Move preview pane: **Left** / **Bottom** / **Top** / **Right**. |
| **`Alt-V`** or **`Ctrl-/`** | Toggle preview pane visibility. |
| **`Shift-Up`** / **`Shift-Down`** | Scroll preview diff pane up / down. |
| **`F1`** or **`Ctrl-O`** | Toggle keybinding help inside preview pane. |

### 🛡️ Safety & Stash Management
| Key | Action |
| :--- | :--- |
| **`Alt-S`** | **Stay Mode**: Arm stay flag so exiting does *not* auto-return. |
| **`Alt-O`** | **Apply Orphan Stash**: Restore stashes left behind by dead sessions. |
| **`Ctrl-W`** | **Quick Hard Reset**: Reset tracked files to HEAD (press twice within 5s). |
| **`Alt-R`** | **Interactive Hard Reset**: Reset tracked files to HEAD (requires typing `YES`). |

### 📋 Export & Branch
| Key | Action |
| :--- | :--- |
| **`Ctrl-Y`** | Copy 7-character short commit hash to clipboard (+ desktop notification). |
| **`Alt-Y`** | Copy 40-character full SHA to clipboard (+ desktop notification). |
| **`Alt-B`** | Create a new branch starting from the selected commit. |

---

## ❓ Frequently Asked Questions (FAQ)

### Q: What happens to my uncommitted changes when I travel back in time?
**A:** They are 100% safe. Before moving HEAD, the script creates a dedicated session stash (`DUSKY_AUTO_STASH_<session_id>`) containing all uncommitted tracked edits. Untracked files in `$HOME` are completely ignored and never touched.

### Q: Does the TUI apply my uncommitted changes onto older commits?
**A:** **No.** Your in-progress work remains sleeping in the git stash ledger. The TUI gives you a pure, authentic snapshot of the repository as it existed on that date. Applying modern in-progress code to historical commits would create merge conflicts.

### Q: What happens if I exit the TUI or press `Ctrl-C` while looking at an old commit?
**A:** You are automatically returned home. The built-in Janitor trap catches `Ctrl-C`, `SIGTERM`, or normal `Esc` exits, switches your worktree back to `main`, and automatically pops your stash.

### Q: How do I stay on an old commit after closing the TUI?
**A:** Press **`Alt-S`** before exiting. The footer will display `ARMED (STAY)`. When you exit, the TUI leaves your terminal detached on that historical commit.

### Q: How do I return to the present after using Stay Mode?
**A:** Simply re-open `dusky_time_machine_tui.sh` and press **`Ctrl-R`** (Return). It reads the saved target from disk, switches back to `main`, and pops your stash.

### Q: Where are my settings and preferences stored?
**A:** In `~/.config/dusky/settings/time_machine_state`. Your preferences for **`VIM_MODE`**, **`PREVIEW_LAYOUT`**, **`PREVIEW_MODE`**, and **`SCOPE`** are saved automatically and remembered across launches.
