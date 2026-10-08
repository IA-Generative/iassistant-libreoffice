# Bundled CA chain for bootstrap HTTPS

This folder contains a CA chain that the plugin SSL context loads in addition
to the system trust store (`ssl.create_default_context()`), unless a configured
`ca_bundle_path` loads first: only the first CA file that loads is used (see
`MainJob.get_ssl_context` and `calc_prompt_function.build_ssl_context`).

File:

- `scaleway-bootstrap-ca-chain.pem`:
  - Let's Encrypt intermediate `R13`
  - ISRG Root `X1`

The plugin loads this bundle automatically from `src/mirai/CAbundle/`.
