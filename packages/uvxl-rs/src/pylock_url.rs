//! The `Pylock` Project-URL in the root's core metadata (M1 to M5, N1).
//! Port of `uvxl/pylock_url.py`.

use anyhow::{Result, bail};
use url::Url;
use uv_pypi_types::{HashAlgorithm, HashDigest};

/// Where the lock is, and the pinned hashes (empty in mutable mode).
#[derive(Debug, Clone)]
pub(crate) struct LockUrl {
    pub(crate) url: Url,
    pub(crate) pinned: Vec<HashDigest>,
}

impl LockUrl {
    pub(crate) fn mode(&self) -> &'static str {
        if self.pinned.is_empty() {
            "mutable"
        } else {
            "pinned"
        }
    }

    pub(crate) fn file_name(&self) -> String {
        let segment = self.url.path_segments().and_then(|mut s| s.next_back());
        percent_decode(segment.unwrap_or_default())
    }
}

/// Rule M1: label normalization of the well-known project URLs spec.
fn normalize_label(label: &str) -> String {
    label
        .chars()
        .filter(|c| !c.is_ascii_punctuation() && !c.is_whitespace())
        .flat_map(char::to_lowercase)
        .collect()
}

/// All `Project-URL` header values, duplicates kept (M2). uv's
/// `Metadata23::project_urls` is a map, which would hide duplicates.
fn project_urls(metadata: &str) -> Vec<(String, String)> {
    metadata
        .lines()
        .take_while(|line| !line.is_empty())
        .filter_map(|line| line.strip_prefix("Project-URL:"))
        .filter_map(|value| value.split_once(','))
        .map(|(label, url)| (label.trim().to_owned(), url.trim().to_owned()))
        .collect()
}

/// Rule N1: the PEP 751 file name pattern `^pylock\.([^.]+\.)?toml$`.
pub(crate) fn is_pylock_name(name: &str) -> bool {
    let Some(middle) = name
        .strip_prefix("pylock.")
        .and_then(|rest| rest.strip_suffix("toml"))
    else {
        return false;
    };
    middle.is_empty() || (middle.ends_with('.') && !middle[..middle.len() - 1].contains('.'))
}

fn percent_decode(segment: &str) -> String {
    url::form_urlencoded::parse(format!("x={segment}").as_bytes())
        .next()
        .map(|(_, value)| value.into_owned())
        .unwrap_or_default()
}

/// Rule M4: only hash parameters (pinned mode). A bad digest fails M4 here.
fn parse_fragment(fragment: Option<&str>) -> Result<Vec<HashDigest>> {
    let mut pinned = Vec::new();
    for parameter in fragment.unwrap_or_default().split('&') {
        if parameter.is_empty() {
            continue;
        }
        let (key, value) = parameter.split_once('=').unwrap_or((parameter, ""));
        let algorithm = match key.to_ascii_lowercase().as_str() {
            "sha256" => HashAlgorithm::Sha256,
            "sha384" => HashAlgorithm::Sha384,
            "sha512" => HashAlgorithm::Sha512,
            _ => bail!("[M4] unknown fragment parameter {parameter:?} in the Pylock URL"),
        };
        match HashDigest::new(algorithm, value.to_ascii_lowercase()) {
            Ok(digest) => pinned.push(digest),
            Err(err) => bail!("[M4] invalid {key} digest in the Pylock URL: {err}"),
        }
    }
    Ok(pinned)
}

/// The root's lock URL. Missing (M5), duplicate (M2) or invalid: fail.
pub(crate) fn find_lock_url(metadata: &str) -> Result<LockUrl> {
    let urls: Vec<String> = project_urls(metadata)
        .into_iter()
        .filter(|(label, _)| normalize_label(label) == "pylock")
        .map(|(_, url)| url)
        .collect();
    let raw = match urls.as_slice() {
        [] => bail!("[M5] the root has no Pylock Project-URL; a locked install is impossible"),
        [one] => one,
        more => bail!("[M2] the root declares {} Pylock URLs", more.len()),
    };
    let mut url = Url::parse(raw).map_err(|err| anyhow::anyhow!("[M3] {raw:?}: {err}"))?;
    // Test-only escape hatch, like `FetchPolicy(allow_http=True)` in Python.
    let allow_http = std::env::var_os("UVXL_TEST_ALLOW_HTTP").is_some();
    if url.scheme() != "https" && !(allow_http && url.scheme() == "http") {
        bail!("[M3] the Pylock URL must be https: {raw:?}");
    }
    let pinned = parse_fragment(url.fragment())?;
    url.set_fragment(None);
    let lock_url = LockUrl { url, pinned };
    if !is_pylock_name(&lock_url.file_name()) {
        bail!(
            "[N1] {:?} in the Pylock URL is not a valid PEP 751 file name",
            lock_url.file_name()
        );
    }
    Ok(lock_url)
}

#[cfg(test)]
mod tests {
    use super::*;

    const HEX: &str = "abababababababababababababababababababababababababababababababab";

    fn metadata(urls: &[&str]) -> String {
        let mut text = String::from("Metadata-Version: 2.3\nName: app\nVersion: 1.0\n");
        for url in urls {
            text.push_str(&format!("Project-URL: {url}\n"));
        }
        text + "\n"
    }

    #[test]
    fn labels_are_normalized() {
        for label in ["Pylock", "PYLOCK", "Py-Lock", "py lock"] {
            let found = find_lock_url(&metadata(&[&format!(
                "{label}, https://h.example/pylock.toml"
            )]))
            .unwrap();
            assert_eq!(found.url.as_str(), "https://h.example/pylock.toml");
            assert_eq!(found.mode(), "mutable");
        }
    }

    #[test]
    fn rule_ids() {
        let cases = [
            (vec!["Homepage, https://h.example/pylock.toml"], "[M5]"),
            (
                vec![
                    "Pylock, https://h.example/pylock.toml",
                    "pylock, https://h.example/pylock.b.toml",
                ],
                "[M2]",
            ),
            (vec!["Pylock, http://h.example/pylock.toml"], "[M3]"),
            (vec!["Pylock, https://h.example/pylock.toml#md5=ab"], "[M4]"),
            (vec!["Pylock, https://h.example/lock.toml"], "[N1]"),
            (
                vec!["Pylock, https://h.example/pylock.tool-1.2.toml"],
                "[N1]",
            ),
        ];
        for (urls, rule) in cases {
            let error = find_lock_url(&metadata(&urls)).unwrap_err().to_string();
            assert!(error.starts_with(rule), "{urls:?}: {error}");
        }
    }

    #[test]
    fn pinned_hash_is_parsed_and_removed() {
        let found = find_lock_url(&metadata(&[&format!(
            "Pylock, https://h.example/pylock.prod.toml#sha256={HEX}"
        )]))
        .unwrap();
        assert_eq!(found.url.as_str(), "https://h.example/pylock.prod.toml");
        assert_eq!(found.mode(), "pinned");
        assert_eq!(found.file_name(), "pylock.prod.toml");
    }
}
