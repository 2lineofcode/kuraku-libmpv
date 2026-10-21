#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/../.."
ROOT="$PWD"
BUILD="$ROOT/build/kuraku"
OUTPUT="$ROOT/build/output"
VERSION="v0.6.0-kuraku.1"
BASE="libmpv-xcframeworks_v0.6.0_macos-universal-video-default.tar.gz"
FILTERS="overlay,equalizer,acompressor,alimiter,dynaudnorm,loudnorm,aformat,aresample"
mkdir -p "$BUILD" "$OUTPUT"

fetch() {
    local url="$1" file="$2" checksum="$3"
    if [ ! -f "$file" ]; then
        curl --fail --location --retry 3 "$url" -o "$file.tmp"
        mv "$file.tmp" "$file"
    fi
    echo "$checksum  $file" | shasum -a 256 -c -
}

fetch "https://github.com/media-kit/libmpv-darwin-build/releases/download/v0.6.0/$BASE" \
    "$BUILD/$BASE" 84d2ad98e046e82c6dc34d8547d76c2afeaee89c0f53032773be8985c95536d6
fetch https://ffmpeg.org/releases/ffmpeg-6.0.tar.xz \
    "$BUILD/ffmpeg-6.0.tar.xz" 57be87c22d9b49c112b6d24bc67d42508660e6b718b3db89c44e47e289137082

mkdir -p "$BUILD/base" "$BUILD/source"
tar -xf "$BUILD/$BASE" --strip-components 1 -C "$BUILD/base"
if [ ! -f "$BUILD/source/configure" ]; then
    tar -xf "$BUILD/ffmpeg-6.0.tar.xz" --strip-components 1 -C "$BUILD/source"
fi
SDK="$(xcrun --sdk macosx --show-sdk-path)"
for arch in arm64 x86_64; do
    mkdir -p "$BUILD/$arch"
    (
        cd "$BUILD/$arch"
        "$BUILD/source/configure" \
            --prefix="$BUILD/$arch/install" \
            --arch="$arch" --target-os=darwin --enable-cross-compile \
            --cc="$(xcrun -f clang)" --cxx="$(xcrun -f clang++)" \
            --sysroot="$SDK" \
            --extra-cflags="-arch $arch -mmacosx-version-min=12.0" \
            --extra-ldflags="-arch $arch -mmacosx-version-min=12.0 -Wl,-headerpad_max_install_names" \
            --disable-autodetect --disable-everything --disable-programs --disable-doc \
            --disable-debug --disable-static --enable-shared \
            --disable-avcodec --disable-avformat --disable-avdevice --disable-postproc \
            --enable-avfilter --enable-swresample --enable-swscale \
            --enable-filter="$FILTERS"
        nice -n 10 make -j2 libavfilter/libavfilter.9.dylib
    )
    library="$BUILD/$arch/libavfilter/libavfilter.9.dylib"
    install_name_tool -id '@rpath/Avfilter.framework/Versions/A/Avfilter' "$library"
    for name in avutil swresample swscale; do
        dependency="$(otool -L "$library" | awk -v name="lib$name." 'index($1,name) {print $1}')"
        if [ -n "$dependency" ]; then
            framework="$(echo "${name:0:1}" | tr '[:lower:]' '[:upper:]')${name:1}"
            install_name_tool -change "$dependency" "@rpath/$framework.framework/Versions/A/$framework" "$library"
        fi
    done
done
FRAMEWORK="$BUILD/base/Avfilter.xcframework/macos-arm64_x86_64/Avfilter.framework"
xcrun lipo -create "$BUILD/arm64/libavfilter/libavfilter.9.dylib" \
    "$BUILD/x86_64/libavfilter/libavfilter.9.dylib" -output "$FRAMEWORK/Versions/A/Avfilter"
xcrun strip -x "$FRAMEWORK/Versions/A/Avfilter"
for framework in "$BUILD/base/"*.xcframework/macos-arm64_x86_64/*.framework; do
    codesign --force --sign - "$framework"
done
ARTIFACT="libmpv-xcframeworks_${VERSION}_macos-universal-video-default.tar.gz"
COPYFILE_DISABLE=1 tar -czf "$OUTPUT/$ARTIFACT" -C "$BUILD" base
(cd "$OUTPUT" && shasum -a 256 "$ARTIFACT" > "$ARTIFACT.sha256")
echo "Built $OUTPUT/$ARTIFACT"
