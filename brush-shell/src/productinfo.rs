//! Information about this shell project.

/// The formal name of this product.
pub const PRODUCT_NAME: &str = "brush";

pub(crate) static GNASH: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

pub(crate) static GNASH_VERSION: std::sync::OnceLock<&'static str> = std::sync::OnceLock::new();

/// Returns the executable version, preserving the upstream brush package version separately.
pub fn product_version() -> &'static str {
    GNASH_VERSION.get().copied().unwrap_or(PRODUCT_VERSION)
}

/// Returns the name chosen by the executable entry point.
pub fn product_name() -> &'static str {
    if GNASH.load(std::sync::atomic::Ordering::Relaxed) {
        "gnash"
    } else {
        PRODUCT_NAME
    }
}

const PRODUCT_HOMEPAGE: &str = env!("CARGO_PKG_HOMEPAGE");
const PRODUCT_REPO: &str = env!("CARGO_PKG_REPOSITORY");

/// The URI to display as the product's homepage.
#[allow(clippy::const_is_empty)]
pub const PRODUCT_DISPLAY_URI: &str = if !PRODUCT_HOMEPAGE.is_empty() {
    PRODUCT_HOMEPAGE
} else {
    PRODUCT_REPO
};

/// The version of the product, in string form.
pub const PRODUCT_VERSION: &str = env!("CARGO_PKG_VERSION");

/// Info regarding the specific version of sources used to build this product.
pub const PRODUCT_GIT_VERSION: &str = git_version::git_version!(
    prefix = "git:",
    cargo_prefix = "cargo:",
    fallback = "unknown:",
    args = ["--always", "--dirty=-modified", "--match", ""]
);

pub(crate) fn get_product_display_str() -> String {
    if GNASH.load(std::sync::atomic::Ordering::Relaxed) {
        format!(
            "gnash version {} ({PRODUCT_GIT_VERSION}), based on brush {PRODUCT_VERSION}",
            product_version()
        )
    } else {
        format!(
            "{PRODUCT_NAME} version {PRODUCT_VERSION} ({PRODUCT_GIT_VERSION}) - {PRODUCT_DISPLAY_URI}"
        )
    }
}
