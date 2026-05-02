# Bundled tools

Third-party binaries used by the build scripts. None of these are checked
into git — fetch them yourself with the commands below.

## FBX2glTF

Used by `import_mixamo_glb.py` to convert Mixamo FBX downloads into GLB
without requiring Blender. Single-file binary, ~10 MB.

Source: https://github.com/godotengine/FBX2glTF/releases (active fork of
the original facebookincubator/FBX2glTF)

### Linux

```bash
curl -L -o FBX2glTF \
  https://github.com/godotengine/FBX2glTF/releases/latest/download/FBX2glTF-linux-x86_64
chmod +x FBX2glTF
```

### macOS (Apple Silicon)

```bash
curl -L -o FBX2glTF \
  https://github.com/godotengine/FBX2glTF/releases/latest/download/FBX2glTF-macos-arm64
chmod +x FBX2glTF
```

### Verify

```bash
./FBX2glTF --version
```
