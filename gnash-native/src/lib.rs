//! Native command modules for gnash. ABI v1 runs commands in isolated workers.
//!
//! Modules are trusted native code. Loading executes their ELF constructors.
//! Descriptor memory must be immutable, valid, and live until the library unloads.
//! See `include/gnash.h` and `docs/gnash-loadables.md` for the C contract.

#[cfg(not(target_os = "linux"))]
compile_error!("gnash native modules currently require Linux and /proc");

use std::collections::BTreeMap;
use std::ffi::{CStr, CString, OsString, c_char, c_int};
use std::fs::File;
use std::io;
use std::os::fd::{AsRawFd, FromRawFd};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::OpenOptionsExt as _;
use std::path::Path;
use std::sync::Arc;

/// Current ABI version.
pub const ABI_VERSION: u32 = 1;
/// Private self-exec protocol. Must be handled before shell initialization.
pub const WORKER_FLAG: &str = "--gnash-native-worker-v1";
/// A command receives writable, NUL-terminated argv including its command name.
pub type RunFunction = unsafe extern "C" fn(c_int, *mut *mut c_char) -> c_int;

/// Immutable command descriptor supplied by a native module.
#[repr(C)]
pub struct CommandDescriptor {
    /// NUL-terminated UTF-8 command name (ASCII identifier or hyphen).
    pub name: *const c_char,
    /// Command entry point; it may use process stdio, cwd, and environment.
    pub run: Option<RunFunction>,
}

// SAFETY: ABI providers promise immutable descriptor memory for its entire lifetime.
unsafe impl Sync for CommandDescriptor {}
// SAFETY: Descriptor pointers refer to immutable, process-wide module data.
unsafe impl Send for CommandDescriptor {}

/// Immutable module descriptor, returned by the exported `gnash_module_v1` function.
#[repr(C)]
pub struct ModuleDescriptor {
    /// Must equal [`ABI_VERSION`].
    pub abi_version: u32,
    /// Must equal `sizeof(struct gnash_module)` for ABI v1.
    pub struct_size: u32,
    /// Number of command descriptors (1 through 4096).
    pub command_count: usize,
    /// Array of `command_count` descriptors.
    pub commands: *const CommandDescriptor,
}

// SAFETY: ABI providers promise immutable descriptor memory for its entire lifetime.
unsafe impl Sync for ModuleDescriptor {}
// SAFETY: Descriptor pointers refer to immutable, process-wide module data.
unsafe impl Send for ModuleDescriptor {}

/// A loaded module, owning its library handle and an immutable copy of its ELF file.
pub struct Module {
    _library: libloading::Library,
    file: Arc<File>,
    commands: BTreeMap<String, RunFunction>,
}

/// A command retains the module even when other registrations are unloaded.
#[derive(Clone)]
pub struct Command {
    module: Arc<Module>,
    name: String,
}

fn invalid(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.into())
}

impl Module {
    /// Loads a trusted module from an immutable snapshot of a regular ELF file.
    /// Replacing, deleting, or overwriting the source does not change loaded code.
    pub fn open(path: &Path) -> io::Result<Arc<Self>> {
        let mut source = std::fs::OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_NONBLOCK)
            .open(path)?;
        if !source.metadata()?.is_file() {
            return Err(invalid("native module must be a regular file"));
        }
        // SAFETY: The name is NUL-terminated and the flags are valid for memfd_create.
        let fd = unsafe {
            libc::memfd_create(
                c"gnash-module".as_ptr(),
                libc::MFD_CLOEXEC | libc::MFD_ALLOW_SEALING,
            )
        };
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: memfd_create returned a new owned descriptor.
        let mut snapshot = unsafe { File::from_raw_fd(fd) };
        io::copy(&mut source, &mut snapshot)?;
        let seals =
            libc::F_SEAL_SEAL | libc::F_SEAL_SHRINK | libc::F_SEAL_GROW | libc::F_SEAL_WRITE;
        // SAFETY: snapshot owns fd and F_ADD_SEALS takes an integer bitmask.
        if unsafe { libc::fcntl(fd, libc::F_ADD_SEALS, seals) } < 0 {
            return Err(io::Error::last_os_error());
        }
        Self::from_file(snapshot)
    }

    fn from_file(file: File) -> io::Result<Arc<Self>> {
        let path = format!("/proc/self/fd/{}", file.as_raw_fd());
        // SAFETY: Loading native code is the explicit purpose of this API. Callers
        // supply trusted modules conforming to the documented ABI and lifetime contract.
        let library =
            unsafe { libloading::Library::new(path) }.map_err(|e| invalid(e.to_string()))?;
        // SAFETY: The module contract defines this exact exported symbol and signature.
        let get_descriptor = unsafe {
            library.get::<unsafe extern "C" fn() -> *const ModuleDescriptor>(b"gnash_module_v1\0")
        }
        .map_err(|e| invalid(e.to_string()))?;
        // SAFETY: The validated symbol has the ABI signature defined above.
        let descriptor = unsafe { get_descriptor() };
        if descriptor.is_null() {
            return Err(invalid("module returned a null descriptor"));
        }
        // SAFETY: The ABI requires a valid, aligned, immutable descriptor pointer.
        let descriptor = unsafe { &*descriptor };
        if descriptor.abi_version != ABI_VERSION
            || usize::try_from(descriptor.struct_size).ok() != Some(size_of::<ModuleDescriptor>())
        {
            return Err(invalid("unsupported gnash module ABI or descriptor size"));
        }
        if !(1..=4096).contains(&descriptor.command_count) || descriptor.commands.is_null() {
            return Err(invalid("invalid native command table"));
        }
        // SAFETY: The ABI guarantees an initialized array of command_count descriptors.
        let entries =
            unsafe { std::slice::from_raw_parts(descriptor.commands, descriptor.command_count) };
        let mut commands = BTreeMap::new();
        for entry in entries {
            if entry.name.is_null() {
                return Err(invalid("null native command name"));
            }
            // SAFETY: The ABI guarantees a valid NUL-terminated command name.
            let name = unsafe { CStr::from_ptr(entry.name) }
                .to_str()
                .map_err(|e| invalid(e.to_string()))?;
            if name.is_empty()
                || !name
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
            {
                return Err(invalid(format!("invalid native command name: {name:?}")));
            }
            let run = entry
                .run
                .ok_or_else(|| invalid("null native command entry point"))?;
            if commands.insert(name.to_owned(), run).is_some() {
                return Err(invalid(format!("duplicate native command: {name}")));
            }
        }
        Ok(Arc::new(Self {
            _library: library,
            file: Arc::new(file),
            commands,
        }))
    }

    /// Returns the names provided by this module, in lexical order.
    pub fn names(&self) -> impl Iterator<Item = &str> {
        self.commands.keys().map(String::as_str)
    }

    /// Acquires a command and its library lifetime.
    pub fn command(self: &Arc<Self>, name: &str) -> Option<Command> {
        self.commands.contains_key(name).then(|| Command {
            module: Arc::clone(self),
            name: name.to_owned(),
        })
    }
}

impl Command {
    /// Returns the immutable ELF file to pass to a worker as an inherited fd.
    pub fn file(&self) -> Arc<File> {
        Arc::clone(&self.module.file)
    }

    /// Returns this command's registered name.
    pub fn name(&self) -> &str {
        &self.name
    }
}

/// Handles the private worker protocol, returning `None` for ordinary invocations.
/// Call this before creating threads, parsing shell options, or loading startup files.
pub fn maybe_dispatch() -> Option<i32> {
    let mut args = std::env::args_os().skip(1);
    if args.next().as_deref() != Some(std::ffi::OsStr::new(WORKER_FLAG)) {
        return None;
    }
    Some(match run_worker(&args.collect::<Vec<_>>()) {
        Ok(code) => code,
        Err(err) => {
            eprintln!("gnash: native worker: {err}");
            126
        }
    })
}

fn run_worker(args: &[OsString]) -> io::Result<i32> {
    let [fd, name, rest @ ..] = args else {
        return Err(invalid("expected module fd and command name"));
    };
    let fd: i32 = fd
        .to_str()
        .ok_or_else(|| invalid("invalid fd"))?
        .parse()
        .map_err(|_| invalid("invalid fd"))?;
    if fd < 3 {
        return Err(invalid("module fd must be at least 3"));
    }
    // Open our own copy; the inherited fd belongs to this private worker protocol.
    let file = File::open(format!("/proc/self/fd/{fd}"))?;
    // SAFETY: fd is a private inherited module descriptor, no Rust value owns it.
    unsafe { libc::close(fd) };
    let module = Module::from_file(file)?;
    let name_str = name
        .to_str()
        .ok_or_else(|| invalid("invalid command name"))?;
    let run = module
        .commands
        .get(name_str)
        .ok_or_else(|| invalid("command not in module"))?;
    let mut strings = std::iter::once(name)
        .chain(rest)
        .map(|s| {
            CString::new(s.as_bytes())
                .map(CString::into_bytes_with_nul)
                .map_err(|e| invalid(e.to_string()))
        })
        .collect::<io::Result<Vec<_>>>()?;
    let argc = c_int::try_from(strings.len()).map_err(|_| invalid("too many arguments"))?;
    let mut argv: Vec<_> = strings
        .iter_mut()
        .map(|s| s.as_mut_ptr().cast::<c_char>())
        .collect();
    argv.push(std::ptr::null_mut());
    // SAFETY: This runs before threads are created; restoring SIGPIPE matches CLI utilities.
    unsafe { libc::signal(libc::SIGPIPE, libc::SIG_DFL) };
    // SAFETY: This worker has no other threads; libc copies the locale setting.
    unsafe { libc::setlocale(libc::LC_ALL, c"".as_ptr()) };
    // SAFETY: argv contains argc writable NUL-terminated strings plus a null sentinel;
    // the owning module and all argument buffers remain live until the call returns.
    let code = unsafe { run(argc, argv.as_mut_ptr()) };
    // SAFETY: Passing null flushes all C output streams in this isolated worker.
    if unsafe { libc::fflush(std::ptr::null_mut()) } != 0 && code == 0 {
        return Ok(1);
    }
    Ok(code)
}
