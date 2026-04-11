# Chainlink Setup for 5 Whys

Chainlink is a CLI issue tracker that gives 5 Whys analyses persistent, structured
storage. Trees become issues with parent-child relationships, action items become
trackable issues with labels, and chains survive across sessions.

This is optional. The 5 Whys skill works without chainlink — you can write trees as
markdown. But chainlink adds: persistence, search, blocking relationships, session
tracking, and falsification cascades.

## Installing Rust

Chainlink is written in Rust. You need cargo.

**⚠️ Before proceeding:** Warn the user that installing Rust and compiling chainlink
can take 10-20 minutes and will consume significant CPU/memory. On low-spec machines
it can make the system sluggish. Ask if now is a good time.

### Linux / macOS

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
source "$HOME/.cargo/env"
```

Add cargo to your shell profile for persistence:

```bash
# Add to ~/.bashrc, ~/.zshrc, or ~/.profile depending on your shell:
echo 'source "$HOME/.cargo/env"' >> ~/.bashrc   # bash
echo 'source "$HOME/.cargo/env"' >> ~/.zshrc    # zsh
```

### Windows

Download and run `rustup-init.exe` from https://rustup.rs/. Follow the installer
prompts. Cargo is added to PATH automatically after restart.

### Verify

```bash
rustc --version
cargo --version
```

## Installing Chainlink

**⚠️ Tell your operator:** Compilation can take 10-20 minutes and may slow their
machine significantly. They should avoid running heavy tasks during the install.

Chainlink's upstream PR for typed relations (tkellogg/chainlink#20) is not yet merged.
Install from Tim's fork:

```bash
cargo install --git https://github.com/tkellogg/chainlink.git chainlink-tracker
```

This compiles from source. On resource-constrained machines (< 2GB RAM), it may be
slow or fail. If it fails with an OOM:

```bash
# Reduce parallel compilation
CARGO_BUILD_JOBS=1 cargo install --git https://github.com/tkellogg/chainlink.git chainlink-tracker
```

If the upstream PR gets merged, you can switch to `cargo install chainlink-tracker`.

Verify:
```bash
chainlink --version
```

## Initializing a 5 Whys Database

Chainlink stores its database in a `.chainlink/` directory. It walks up from the
current directory to find the nearest one. This means **where you run `chainlink
init` determines which database you use.**

For 5 Whys, initialize chainlink **inside your home repo** in a dedicated subdirectory:

```bash
# Inside your agent's home repo
mkdir -p rca
cd rca
chainlink init
```

Then add the database to `.gitignore` so the binary DB isn't committed:

```bash
# In your repo root .gitignore, add:
echo 'rca/.chainlink/issues.db' >> .gitignore
```

The `rca/` directory stays inside your repo, so it's easy to find and back up, but
the SQLite database doesn't clutter git history. If you also use chainlink for task
tracking, initialize that in a **separate directory** — don't mix RCA chains and
task backlogs in one database.

**Why separate databases?** RCA chains and task backlogs serve different purposes.
RCA chains are investigative — they branch, they have falsification relationships,
they close when you understand something. Task issues are operational — they close
when you've done something. Mixing them creates noise in both directions.

## Verifying the Setup

```bash
cd rca
chainlink issue list
# Should show: (empty, no issues)
```

## Troubleshooting

**`chainlink: command not found` after install:**

```bash
source "$HOME/.cargo/env"
```

If that fixes it but it doesn't persist across terminals, make sure your shell profile
loads cargo (see the Rust install section above).
