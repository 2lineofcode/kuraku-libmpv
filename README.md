# KuraKu libmpv

macOS universal media libraries for KuraKu, based on media-kit's `v0.6.0` release and FFmpeg 6.0.

The build keeps the upstream playback frameworks and replaces `Avfilter` with an ABI-compatible build enabling `dynaudnorm`, `loudnorm`, `acompressor`, `alimiter`, `aformat`, and `aresample`. Existing `overlay` and `equalizer` filters remain enabled. Both arm64 and x86_64 are included; macOS 12+ is required.

## Build and verify

Requires Xcode command-line tools, Python 3.12+, and nasm (`brew install nasm`).

```sh
bash scripts/kuraku/build.sh
python3 -B test/audio_normalization.py build/kuraku/base
```

The script verifies pinned upstream downloads, builds with two workers, and writes the framework archive and SHA-256 checksum to `build/output/`. FFmpeg sources and intermediate objects stay under the ignored `build/kuraku/` directory. Only FFmpeg's audio filtering library is replaced; codecs and libmpv remain the upstream binaries.

Publish a new immutable tag and its verified archive together. KuraKu's local `media_kit_libs_macos_video` override pins the release URL and checksum; update both when releasing a new build. CI verifies the build and audio output but does not replace published release assets.

See [upstream documentation](docs/upstream.md) and [licenses](LICENSE.txt).
