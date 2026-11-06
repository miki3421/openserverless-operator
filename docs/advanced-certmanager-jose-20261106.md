# cert-manager controller: go-jose disposition

**Outcome: no runtime change.** The three remaining rated package matches in
the qualified upstream controller are not reachable through the inspected
controller paths. This is an artifact-specific assessment, not a dependency
upgrade, scanner suppression, general Vault assessment or independent review.

Scope: cert-manager 1.21.3, upstream commit
`925c22283de556172d48d96a165dc2fa27f7a6ff`, Linux AMD64 controller image index
`sha256:9f4f96a33c7774cef24466fcd85031997531a03a8d117f151126baa8d1290dcc`
and AMD64 child manifest
`sha256:9b262e9493ab3e0daebc2c9cfbddc6df68b9ac91afc036a759584f3d49d607e5`.
The extracted controller binary SHA256 is
`8457856e1c678b448bb81bead53b17486ed142f5b85b05315d70c4a381c2d37f`.
The ADV pod references that same image index. Build metadata confirms Go 1.26.10
and go-jose/v4 4.1.4; the scanner's version identification is correct.

The dependency chain is controller → issuer/vault → internal/vault → Vault API
1.23.0 → go-jose. The only production Go import of go-jose/v4 found in the loaded
dependency sources is Vault API `plugin_helpers.go`. It implements the legacy
`VaultPluginTLSProviderContext` plugin bootstrap using a wrapped ES512 JWT.
Cert-manager does not call that helper or its `PluginAPIClientMeta` wrapper.
Its ordinary Vault issuer uses the API client, not the Vault plugin bootstrap.

The binary's Go PC table was parsed using Go 1.26.10 `debug/elf` and `debug/gosym`:
110,139 functions were recovered. The retained go-jose symbols are package
initialization, JSON Number methods, float encoding and an error type. Neither
Vault plugin bootstrap function, JWK/RSA decoding, JWE decryption nor the
recursive JSON decoder is present. This corroborates the source trace for:

- CVE-2026-116585: empty JWE ciphertext decryption panic.
- CVE-2026-116586: recursive JSON decoding exhaustion.
- CVE-2026-116587: excessive RSA parameters during JWK decoding.

The scanner continues to report 1 High and 2 Medium package matches. No ignore
file or VEX exception was installed, and no claim of a patched dependency is
made. Re-evaluate this assessment for every changed binary, dependency graph,
architecture or newly introduced Vault plugin/JWK/JWE entry point.

Upstream [4.1.5](https://github.com/go-jose/go-jose/releases/tag/v4.1.5) and
[4.1.6](https://github.com/go-jose/go-jose/releases/tag/v4.1.6) contain the fixes.
They also change behavior: `RandReader` is removed, and JWK parsing defaults to
a 4096-bit RSA limit. A future upgrade should preserve ordinary certificate
issuance, Vault authentication and supported key sizes. There is no demonstrated
reachable vulnerability in this exact controller that requires replacing the
official image with a private rebuild now.
