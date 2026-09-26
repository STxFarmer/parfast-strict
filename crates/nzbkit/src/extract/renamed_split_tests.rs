//! GH #92: a RAR split member whose continuation is stored under a
//! different name. `rars` extracts it under the first fragment's name
//! (a939dae3a); these pin that the one-pass extractor does too - in
//! every arrival order, for both RAR families - and that a pair which is
//! NOT one member keeps its names and falls back as before.

use super::*;
use crate::rar::fixtures;

use super::testutil::*;

const N: usize = 300_000;

/// Three store volumes of one member, the pieces stored under `names`,
/// the middle and last declaring `sizes[1]` / `sizes[2]` as the member's
/// whole size (a real set declares the same on all three).
fn rar4_set(names: [&str; 3], sizes: [u64; 3], data: &[u8]) -> Vec<Vec<u8>> {
    let (a, b) = (N / 3, 2 * N / 3);
    let piece = |i: usize| match i {
        0 => &data[..a],
        1 => &data[a..b],
        _ => &data[b..],
    };
    (0..3)
        .map(|i| {
            fixtures::with_rar4_end_block(
                fixtures::rar4_volume(&[(names[i], sizes[i], piece(i), i > 0, i < 2)]),
                i < 2,
            )
        })
        .collect()
}

fn rar5_set(names: [&str; 3], data: &[u8]) -> Vec<Vec<u8>> {
    let (a, b) = (N / 3, 2 * N / 3);
    let n = N as u64;
    fixtures::rar5_volume_set(&[
        &[(names[0], n, &data[..a], false, true)],
        &[(names[1], n, &data[a..b], true, true)],
        &[(names[2], n, &data[b..], true, false)],
    ])
}

/// Feed `vols` as `set.partNN.rar` in `order`, and finish.
fn run(
    tag: &str,
    vols: &[Vec<u8>],
    order: &[usize],
) -> (ExtractReport, Vec<String>, crate::testscratch::ScratchDir) {
    let dir = tmpdir(tag);
    let ex = Extractor::new(&dir, vols.len(), true);
    for &vi in order {
        let vol = &vols[vi];
        let name = format!("set.part{:02}.rar", vi + 1);
        for s in (0..vol.len()).step_by(7000) {
            let e = (s + 7000).min(vol.len());
            ex.write(vi, &name, vol.len() as u64, s as u64, &vol[s..e])
                .unwrap();
        }
    }
    let rep = ex.finish().unwrap();
    let files = dir_files(&dir);
    (rep, files, dir)
}

const ORDERS: [[usize; 3]; 6] = [
    [0, 1, 2],
    [2, 1, 0],
    [1, 2, 0],
    [2, 0, 1],
    [1, 0, 2],
    [0, 2, 1],
];

/// The member extracts one-pass, byte-exact, under the FIRST fragment's
/// name - and nothing is left under a continuation's name - whenever the
/// TAIL volume lands last, which is the ordinary shape of a download
/// fetched in NZB order.
///
/// When it lands earlier, the tail has already written itself out under
/// its own stored name (a tail anchors on its own size, and a middle
/// piece linked to it resolves from that anchor too), so it keeps that
/// name, the adoption declines, and the set falls back cleanly to the
/// post-pass - which is where every renamed set went before this fix.
/// The line that must hold in EVERY order is the second half: a
/// fallen-back set reports no output, never a member with a hole in it.
///
/// `renamed = false` is the control: a same-name set is one-pass in
/// every order, exactly as before.
fn assert_one_pass(tag: &str, vols: &[Vec<u8>], data: &[u8], renamed: bool) {
    for (k, order) in ORDERS.iter().enumerate() {
        let (rep, files, dir) = run(&format!("{tag}{k}"), vols, order);
        if renamed && order[2] != 2 {
            assert!(
                !rep.fallbacks.is_empty(),
                "{tag} {order:?}: expected a clean fallback, got {:?} / {files:?}",
                rep.extracted
            );
            assert!(
                rep.extracted.is_empty(),
                "{tag} {order:?}: a fallen-back set reported output {:?}",
                rep.extracted
            );
            continue;
        }
        assert!(
            rep.fallbacks.is_empty(),
            "{tag} {order:?}: fell back {:?}",
            rep.fallbacks
        );
        assert_eq!(
            rep.extracted,
            vec![("movie.mkv".to_string(), N as u64)],
            "{tag} {order:?}"
        );
        assert_eq!(files, vec!["movie.mkv".to_string()], "{tag} {order:?}");
        assert!(
            std::fs::read(dir.join("movie.mkv")).unwrap() == data,
            "{tag} {order:?}: bytes"
        );
    }
}

#[test]
fn a_continuation_renamed_by_case_extracts_one_pass_under_the_first_name() {
    let data = payload(N, 92);
    let n = N as u64;
    let vols = rar4_set(["movie.mkv", "MOVIE.MKV", "Movie.Mkv"], [n; 3], &data);
    assert_one_pass("gh92case", &vols, &data, true);
}

#[test]
fn a_continuation_renamed_outright_extracts_one_pass_under_the_first_name() {
    let data = payload(N, 93);
    let n = N as u64;
    let vols = rar4_set(
        ["movie.mkv", "other.bin", "a-longer-third-name.dat"],
        [n; 3],
        &data,
    );
    assert_one_pass("gh92other", &vols, &data, true);
}

#[test]
fn the_rar5_twin_extracts_one_pass_under_the_first_name() {
    let data = payload(N, 94);
    let vols = rar5_set(["movie.mkv", "Movie.MKV", "renamed.bin"], &data);
    assert_one_pass("gh92r5", &vols, &data, true);
}

/// The control: the ordinary same-name set is untouched by the rule.
#[test]
fn the_same_name_set_still_extracts_one_pass() {
    let data = payload(N, 95);
    let n = N as u64;
    let vols = rar4_set(["movie.mkv"; 3], [n; 3], &data);
    assert_one_pass("gh92same", &vols, &data, false);
}

/// A continuation that declares a different whole-member size is not
/// the same member, so it keeps its name - and the set falls back, as
/// every renamed set did before, rather than being stitched together.
#[test]
fn a_continuation_of_a_different_size_is_not_adopted() {
    let data = payload(N, 96);
    let n = N as u64;
    let vols = rar4_set(
        ["movie.mkv", "other.bin", "other.bin"],
        [n, n + 1, n + 1],
        &data,
    );
    for (k, order) in ORDERS.iter().enumerate() {
        let (rep, files, _) = run(&format!("gh92size{k}"), &vols, order);
        assert!(
            !rep.extracted.iter().any(|(n, _)| n == "movie.mkv"),
            "{order:?}: a different member was stitched onto movie.mkv: {:?}",
            rep.extracted
        );
        assert!(
            !rep.fallbacks.is_empty(),
            "{order:?}: expected a fallback, files {files:?}"
        );
    }
}

/// Only ADJACENT volumes adopt: with the middle volume missing, the
/// last one's renamed piece has no predecessor to take a name from.
#[test]
fn a_gap_in_the_set_adopts_nothing() {
    let data = payload(N, 97);
    let n = N as u64;
    let vols = rar4_set(["movie.mkv", "movie.mkv", "other.bin"], [n; 3], &data);
    let dir = tmpdir("gh92gap");
    let ex = Extractor::new(&dir, 3, true);
    for vi in [0usize, 2] {
        let vol = &vols[vi];
        let name = format!("set.part{:02}.rar", vi + 1);
        ex.write(vi, &name, vol.len() as u64, 0, vol).unwrap();
    }
    let inner = ex.inner.lock_ok();
    let names: Vec<String> = inner
        .slots
        .iter()
        .filter_map(|s| s.mapper.as_ref())
        .flat_map(|m| m.entries.iter().map(|e| e.name.clone()))
        .collect();
    assert!(
        names.contains(&"other.bin".to_string()),
        "renamed across a gap: {names:?}"
    );
}
