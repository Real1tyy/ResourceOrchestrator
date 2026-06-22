//! `roch` — CLI entry point (skeleton). Subcommands land via the specs under `docs/specs/`.

fn main() {
    roch_daemon::telemetry::init_tracing();
    tracing::info!("roch starting (skeleton)");
}
