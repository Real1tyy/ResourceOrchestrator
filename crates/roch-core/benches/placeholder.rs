//! Placeholder criterion benchmark — establishes the perf-bench harness from day one.
//!
//! Replace the benched body with real `roch-core` hot paths (scheduler admission, fingerprint
//! hashing, state-machine transitions) as they land via the specs. Run with `mise run perf:bench`.

use std::hint::black_box;

use criterion::{criterion_group, criterion_main, Criterion};

fn placeholder(c: &mut Criterion) {
    c.bench_function("placeholder_sum", |b| {
        b.iter(|| (0u64..black_box(1_000)).sum::<u64>());
    });
}

criterion_group!(benches, placeholder);
criterion_main!(benches);
