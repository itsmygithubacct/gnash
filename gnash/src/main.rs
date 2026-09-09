//! gnash command-line entry point.
fn main() {
    brush_shell::entry::run_gnash(env!("CARGO_PKG_VERSION"));
}
