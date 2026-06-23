# Third-party notices

ShowControl is MIT-licensed (see [LICENSE](LICENSE)). It builds on the following
permissively-licensed components. No GPL/LGPL anywhere - keep it that way as the
roadmap grows.

## tinyosc (ISC) - bundled

The OSC extension's C shim builds on **tinyosc** by Martin Roth, an ISC-licensed
single-file OSC codec. It is **vendored** in this repository at
[`src/third_party/tinyosc/`](src/third_party/tinyosc/); the upstream license is
retained verbatim at `src/third_party/tinyosc/LICENSE`.

- Upstream: https://github.com/mhroth/tinyosc
- License: ISC (use for any purpose incl. commercial/closed-source; retain the notice)

## RtMidi (modified MIT) - fetched at build time

The MIDI extension's C shim binds **RtMidi** by Gary P. Scavone, a
modified-MIT-licensed realtime MIDI I/O library. It is **fetched by CMake**
(`FetchContent`, pinned `GIT_TAG 6.0.0`) and statically linked into the `midi`
shared library; its `LICENSE` travels in the fetched source tree and should be
included in the packaged `midi` extension.

- Upstream: https://github.com/thestk/rtmidi
- License: modified MIT (MIT-style, with a courtesy request that modifications be
  sent upstream - good citizenship; contribute fixes back)

## Art-Net (royalty-free protocol) - no bundled code

The Art-Net extension is a clean-room, pure-LCB implementation of the openly
published Art-Net protocol. There is **no third-party library to bundle** and so
no upstream license to ship.

> "Art-Net" is a trademark of Artistic Licence. Implementing the protocol is
> free; this extension is described as **"Art-Net compatible"** and implies no
> endorsement, nor uses the mark as the product name.
