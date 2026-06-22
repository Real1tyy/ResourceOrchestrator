//! `roch-stress` — backend stress/load harness (skeleton).
//!
//! Home for the PRD's core scenario: N concurrent agents submitting pipelines to the daemon
//! at once (UC-2), measuring throughput, queue depth, and contention under load. Run with
//! `mise run test:stress`. The real concurrent-submission load loop lands with the daemon
//! (MVP-1); this skeleton establishes where stress tests live and how they are invoked.

fn main() {
    println!(
        "roch-stress: skeleton — concurrent-submission load loop lands with the daemon (MVP-1)."
    );
}
