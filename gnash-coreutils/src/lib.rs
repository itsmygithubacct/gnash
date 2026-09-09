//! uutils coreutils exposed as a gnash native module.

use std::ffi::{CStr, CString, OsString, c_char, c_int};
use std::os::unix::ffi::OsStrExt;
use std::sync::OnceLock;

use gnash_native::{CommandDescriptor, ModuleDescriptor};

static MODULE: OnceLock<ModuleDescriptor> = OnceLock::new();

/// Returns the immutable native command table.
#[unsafe(no_mangle)]
pub extern "C" fn gnash_module_v1() -> *const ModuleDescriptor {
    MODULE.get_or_init(|| {
        let mut names: Vec<_> = brush_coreutils_builtins::bundled_commands()
            .into_keys()
            .collect();
        names.sort();
        let commands: Vec<_> = names
            .into_iter()
            .filter_map(|name| {
                CString::new(name).ok().map(|name| CommandDescriptor {
                    name: name.into_raw(),
                    run: Some(run),
                })
            })
            .collect();
        let commands = Box::leak(commands.into_boxed_slice());
        ModuleDescriptor {
            abi_version: gnash_native::ABI_VERSION,
            struct_size: u32::try_from(size_of::<ModuleDescriptor>()).unwrap_or(0),
            command_count: commands.len(),
            commands: commands.as_ptr(),
        }
    })
}

unsafe extern "C" fn run(argc: c_int, argv: *mut *mut c_char) -> c_int {
    let Ok(argc) = usize::try_from(argc) else {
        return 126;
    };
    if argc == 0 || argv.is_null() {
        return 126;
    }
    // SAFETY: The native ABI supplies argc initialized argument pointers.
    let argv = unsafe { std::slice::from_raw_parts(argv, argc) };
    let args: Vec<OsString> = argv
        .iter()
        .map(|arg| {
            // SAFETY: Every pointer is a NUL-terminated string live for this call.
            let bytes = unsafe { CStr::from_ptr(*arg) }.to_bytes();
            std::ffi::OsStr::from_bytes(bytes).to_owned()
        })
        .collect();
    let Some(name) = args[0].to_str() else {
        return 126;
    };
    match brush_coreutils_builtins::bundled_commands().get(name) {
        Some(command) => command(args),
        None => 127,
    }
}
