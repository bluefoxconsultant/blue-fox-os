# Anaconda installer chrome — asset spec

Drop final Anaconda branding assets in this directory so
`scripts/inject-anaconda-product.sh` can overlay them into the install-time
squashfs (`/images/install.img`). The overlay step is skipped when an asset
is missing, so a partial drop is safe — only files that are present land in
the ISO. Olivier produces the PNGs himself; this README is the spec the
PNGs need to satisfy.

Tracks Odoo tasks **BF #22417** (top-left logo, closed: covered by
`sidebar-bg.png` wordmark — `.product-logo` slot neutralized in CSS),
**BF #22418** (sidebar pattern), and the GUI-title half of **BF #22419**
(« BLUE FOX OS INSTALLATION »). The runtime keyboard + locale halves of
#22419 are handled by boot params in `scripts/brand-iso.sh` and don't
need anything here.

## Files consumed by the overlay

All four files are optional individually. The placeholders in this directory
exercise the overlay path end-to-end against the existing `branding/logo.png`
so CI smoke builds don't bit-rot — replace them with the final designs.

| File                        | Target path in install.img                                  | Spec                                                                                                                          |
| --------------------------- | ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `blue-fox-os.conf`          | `/etc/anaconda/profile.d/blue-fox-os.conf`                  | Profile config selected by `inst.profile=blue-fox-os`. Derives from `fedora-kinoite`. Plain text — already provided here.    |
| `blue-fox-os.css`           | `/usr/share/anaconda/pixmaps/blue-fox-os.css`               | GTK4 stylesheet for sidebar + headerbar + suggested-action accents. Already provided here.                                    |
| `sidebar-bg.png`            | `/usr/share/anaconda/pixmaps/sidebar-bg.png`                | Sidebar background **including BF wordmark + fox glyph at top** (Olivier's « Barracuda Side Graphic », 1540×6400 PNG). `background-size: cover` + `center top` crops to the 256×800 sidebar slot. |
| ~~`sidebar-logo.png`~~      | ~~`/usr/share/anaconda/pixmaps/sidebar-logo.png`~~          | **No longer needed (#22417)**. The wordmark + glyph live inside `sidebar-bg.png`; the `.product-logo` overlay slot is neutralized in the CSS to avoid duplicating the brand. |

## Brand reference

`reference_bf_brand_canonical.md` in tentaclaude memory is the source of
truth for the palette + typography:

- Primary blue: `#29ABE1` (decoration / accent only — never foreground text on dark)
- Anthracite: `#2D3031`
- Typeface: Lexend

`branding/blue_fox_os_lossless.svg` is the canonical BFOS logotype, kept
in the repo as the source of truth for re-deriving any future brand asset.

## Validation

After dropping assets, rebuild the ISO and boot in QEMU:

```sh
sudo SLUG=bf ./scripts/build-iso.sh
sudo qemu-system-x86_64 -enable-kvm -m 4G -cdrom output/bootiso/install.iso \
    -bios /usr/share/OVMF/OVMF_CODE.fd
```

Confirm at the Anaconda install screen:

- Title bar reads « BLUE FOX OS INSTALLATION » (sourced from `/.buildstamp`
  inside stage2 — see Open question below)
- Sidebar shows BF pattern + logo instead of the Fedora triangle (CSS +
  pixmaps loaded via the `blue-fox-os.conf` profile selected by
  `inst.profile=blue-fox-os`)
- Keyboard indicator top-right reads `ca` (runtime fix, boot param
  `inst.keymap=ca`)

## Open question — `.buildstamp`

The installer title bar string « FEDORA LINUX 43 INSTALLATION » is composed
from `Product` + `Version` in `/.buildstamp` inside stage2 — not the profile
config. To fully close the title half of #22419, the overlay also needs to
patch `/.buildstamp` (set `Product=Blue Fox OS`, `Version=0.1.1`). The
overlay script ships a `buildstamp.ini` placeholder for this — to be
verified on the first real boot test after this PR merges.

If the title still says « FEDORA LINUX 43 » after a real boot:

1. Mount the branded ISO and `unsquashfs images/install.img`.
2. `cat squashfs-root/.buildstamp` — confirm the `Product=` line was overlaid.
3. If it's still `Product=Fedora`, the overlay path lost it — debug
   `scripts/inject-anaconda-product.sh`.
