FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        gnupg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && curl -fsSL https://opencode.ai/install | bash \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /usr/local/bin/devbox-entrypoint
COPY git-credential-devbox.sh /usr/local/bin/git-credential-devbox
COPY gh-wrapper.sh /usr/local/bin/gh-wrapper

RUN chmod +x /usr/local/bin/devbox-entrypoint /usr/local/bin/git-credential-devbox /usr/local/bin/gh-wrapper \
    && mkdir -p /opt/devbox-bin \
    && ln -s /usr/local/bin/gh-wrapper /opt/devbox-bin/gh

ENV PATH="/opt/devbox-bin:${PATH}"

ENTRYPOINT ["/usr/local/bin/devbox-entrypoint"]
CMD ["sleep", "infinity"]
