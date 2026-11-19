# Traefik Cookie Path Replacement

This Yaegi middleware rewrites the `Path` attribute of response cookies. Rules can match the cookie name and path with regular expressions and use named path captures in the replacement.

## Load a released version locally

Clone a tag underneath the working directory used by Traefik, preserving the full module path:

```sh
git clone --depth 1 --branch v0.1.0 \
  https://github.com/michibiki-io/TraefikCookiePathReplacement.git \
  plugins-local/src/github.com/michibiki-io/TraefikCookiePathReplacement
```

Add the plugin to Traefik's static configuration and restart Traefik:

```yaml
experimental:
  localPlugins:
    cookiePathReplacement:
      moduleName: github.com/michibiki-io/TraefikCookiePathReplacement
```

If Traefik runs in a container, mount the checkout at `/plugins-local/src/github.com/michibiki-io/TraefikCookiePathReplacement` in the container.

This example uses Traefik's file provider. Its template action emits the literal named-capture placeholders for the plugin. Attach the middleware to the router that serves the upstream application:

```yaml
http:
  middlewares:
    cookiePathReplacement:
      plugin:
        cookiePathReplacement:
          replacements:
            - name_regex: "test(.*)"
              original: "(?P<before>.*)/old/(?P<after>.*)"
              replacement: "{{ `{{before}}/new/{{after}}` }}"
```

For example, `test-session=value; Path=/app/old/login` becomes `test-session=value; Path=/app/new/login`. Leave the name filter empty to match any cookie name. Existing configurations can still use `name` as an alias. A nonempty `name_regex` takes precedence.

## Development

Use the Go version declared in [go.mod](go.mod). The plugin and its Go tests use the standard library. Run `make build lint test` to compile the package, run `go vet`, and run the Go tests with the race detector.

Run `make test-traefik` with Python 3 and Docker on Linux to test a real Traefik v3.7.19 instance. The test sends two cookies with the same path and verifies that only the name match is rewritten. Run `make test-release` to exercise release planning without publishing.

The Test workflow builds and tests each PR head on native Linux AMD64 and ARM64 runners. A push to `main` publishes tagged Yaegi source only when its tip exactly matches the result of a merged PR. Release versions and notes use the original PR commits.

Mark a breaking change with `!` before the colon in a Conventional Commit subject, for example `feat(config)!: rename a setting`. The release workflow also recognizes `BREAKING CHANGE:` and `BREAKING-CHANGE:` in consecutive footer paragraphs at the end of the commit message. Each footer paragraph starts with a trailer such as `Refs: #42` or `Refs #42`. Use `!` in the subject when the breaking-change explanation spans multiple paragraphs.
