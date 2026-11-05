# Isolated dev container: uv, Rust and Claude Code, running Claude Code in
# remote control mode on the workspace mounted at /workspace.
FROM ubuntu:24.04

ARG USERNAME=dev
ARG USER_UID=1000
ARG USER_GID=1000
ARG RUST_TOOLCHAIN=stable

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    RUSTUP_HOME=/opt/rustup \
    CARGO_HOME=/opt/cargo \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    UV_LINK_MODE=copy \
    PATH=/opt/cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Base tools Claude Code, uv and cargo rely on; build-essential for the linker.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl git jq less openssh-client \
        pkg-config procps ripgrep sudo unzip \
    && rm -rf /var/lib/apt/lists/*

# Ubuntu 24.04 ships a user "ubuntu" with UID/GID 1000: remove it so ours fits.
RUN if id ubuntu >/dev/null 2>&1; then userdel -r ubuntu; fi \
    && groupadd --gid "${USER_GID}" "${USERNAME}" \
    && useradd --uid "${USER_UID}" --gid "${USER_GID}" -m -s /bin/bash "${USERNAME}" \
    && echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/${USERNAME}" \
    && chmod 0440 "/etc/sudoers.d/${USERNAME}" \
    && mkdir -p /opt/uv-python /workspace \
    && chown "${USER_UID}:${USER_GID}" /opt/uv-python /workspace

# uv (system wide).
RUN curl -LsSf https://astral.sh/uv/install.sh \
    | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh

# Rust (system wide, owned by the user so cargo/rustup can update it).
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
    | sh -s -- -y --no-modify-path --profile minimal \
        --default-toolchain "${RUST_TOOLCHAIN}" -c rustfmt -c clippy \
    && chown -R "${USER_UID}:${USER_GID}" /opt/rustup /opt/cargo

USER ${USERNAME}
ENV PATH=/home/${USERNAME}/.local/bin:${PATH}

# Mount points of the compose volumes: a new named volume copies the
# ownership of its mount point, so these must exist and belong to the user.
RUN mkdir -p ~/venv ~/.cache/uv ~/cargo-target ~/.claude-config /opt/cargo/registry

# Pythons for the workspace (requires-python >= 3.12).
RUN uv python install 3.12 3.14

# Claude Code (native installer, into ~/.local/bin).
RUN curl -fsSL https://claude.ai/install.sh | bash \
    && claude --version

WORKDIR /workspace
CMD ["claude", "remote-control"]
