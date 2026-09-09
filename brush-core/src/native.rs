//! Registration and invocation of gnash native command modules.

use std::sync::Arc;

use futures::future::BoxFuture;

use crate::{
    CommandArg, Error, ExecutionContext, ExecutionResult, Shell, ShellExtensions, builtins,
    commands,
};

/// Opens a trusted gnash native module.
pub fn load_module(path: &std::path::Path) -> std::io::Result<Arc<gnash_native::Module>> {
    gnash_native::Module::open(path)
}

/// Native implementation and original builtin registration retained for unloading.
#[derive(Clone)]
pub struct Registration<SE: ShellExtensions> {
    /// Command and the module that owns its code.
    pub command: gnash_native::Command,
    /// Original registration to restore, if the load replaced a static builtin.
    pub previous: Option<Box<builtins::Registration<SE>>>,
}

impl<SE: ShellExtensions> Shell<SE> {
    /// Registers selected commands from a native module. Validation is atomic.
    /// An empty name list registers no commands; callers can use `module.names()` for all.
    pub fn load_native_module(
        &mut self,
        module: &Arc<gnash_native::Module>,
        names: &[String],
    ) -> Result<(), Error> {
        let commands = names
            .iter()
            .map(|name| {
                let command = module.command(name).ok_or_else(|| {
                    std::io::Error::new(
                        std::io::ErrorKind::NotFound,
                        format!("module does not provide {name}"),
                    )
                })?;
                if self
                    .builtins()
                    .get(name)
                    .is_some_and(|builtin| builtin.special_builtin)
                {
                    return Err(std::io::Error::other(format!(
                        "cannot replace special builtin {name}"
                    )));
                }
                Ok(command)
            })
            .collect::<std::io::Result<Vec<_>>>()?;
        for command in commands {
            let name = command.name().to_owned();
            let previous = self.builtins().get(&name).cloned().and_then(|old| {
                if let Some(native) = old.native {
                    native.previous
                } else {
                    Some(Box::new(old))
                }
            });
            self.register_builtin(
                name,
                builtins::Registration {
                    execute_func: execute,
                    content_func: content,
                    disabled: false,
                    special_builtin: false,
                    declaration_builtin: false,
                    native: Some(Registration { command, previous }),
                },
            );
        }
        Ok(())
    }

    /// Unloads a runtime command, restoring the original builtin if one was replaced.
    pub fn unload_native_builtin(&mut self, name: &str) -> Result<(), Error> {
        let native = self
            .builtins()
            .get(name)
            .and_then(|builtin| builtin.native.clone())
            .ok_or_else(|| std::io::Error::other(format!("{name}: not a loaded builtin")))?;
        self.remove_builtin_registration(name);
        if let Some(previous) = native.previous {
            self.register_builtin(name, *previous);
        }
        Ok(())
    }
}

fn execute<SE: ShellExtensions>(
    context: ExecutionContext<'_, SE>,
    args: Vec<CommandArg>,
) -> BoxFuture<'_, Result<ExecutionResult, Error>> {
    // Direct invocations through `builtin` also go through native command dispatch.
    Box::pin(async move {
        let mut command = commands::SimpleCommand::new(
            commands::ShellForCommand::ParentShell(context.shell),
            context.params,
            context.command_name,
            args,
        );
        command.use_functions = false;
        Ok(command.execute().await?.wait().await?.into())
    })
}

#[allow(
    clippy::unnecessary_wraps,
    reason = "builtin content callback signature"
)]
fn content(
    name: &str,
    _content_type: builtins::ContentType,
    _options: &builtins::ContentOptions,
) -> Result<String, Error> {
    Ok(format!(
        "{name}: native command (gnash ABI v1). Run `{name} --help` for usage.\n"
    ))
}
