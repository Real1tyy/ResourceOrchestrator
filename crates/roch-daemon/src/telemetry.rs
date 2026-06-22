//! Tracing/telemetry initialization — the backend observability seam.
//!
//! Sets up `tracing-subscriber` with an env-filter (so `RUST_LOG` controls verbosity) and a
//! fmt layer. Structured JSON output, a rotating file appender, and per-run spans land with
//! the daemon (MVP); this establishes structured tracing from day one.

use tracing_subscriber::layer::SubscriberExt;
use tracing_subscriber::util::SubscriberInitExt;
use tracing_subscriber::EnvFilter;

/// Initialize the global tracing subscriber. Call once at process start; `RUST_LOG`
/// overrides the default `info` filter.
pub fn init_tracing() {
    let filter = EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info"));
    tracing_subscriber::registry()
        .with(filter)
        .with(tracing_subscriber::fmt::layer())
        .init();
}
