1. dont run the offline package isntaller from the root of /mnt/zram cuz the `lost+found` dir will cause issues because of permissions issues

download these pacakges first 

```bash
sudo pacman -Syu --needed --noconfirm archiso
```


> [!NOTE]- makepkg.conf in .config/pacman/makepkg.conf for general compilation of builds
> ```ini
> #!/hint/bash
> # shellcheck disable=2034
> 
> #
> # /etc/makepkg.conf
> # -----------------------------------------------------------------------------
> # Optimized for Mass Distribution / Offline ISO Generation
> # Balances extreme compilation speed with universal hardware compatibility.
> # -----------------------------------------------------------------------------
> 
> #########################################################################
> # SOURCE ACQUISITION
> #########################################################################
> #
> DLAGENTS=('file::/usr/bin/curl -qgC - -o %o %u'
>           'ftp::/usr/bin/curl -qgfC - --ftp-pasv --retry 3 --retry-delay 3 -o %o %u'
>           'http::/usr/bin/curl -qgb "" -fLC - --retry 3 --retry-delay 3 -o %o %u'
>           'https::/usr/bin/curl -qgb "" -fLC - --retry 3 --retry-delay 3 -o %o %u'
>           'rsync::/usr/bin/rsync --no-motd -z %u %o'
>           'scp::/usr/bin/scp -C %u %o')
> 
> VCSCLIENTS=('bzr::breezy'
>             'fossil::fossil'
>             'git::git'
>             'hg::mercurial'
>             'svn::subversion')
> 
> #########################################################################
> # ARCHITECTURE, COMPILE FLAGS
> #########################################################################
> #
> CARCH="x86_64"
> CHOST="x86_64-pc-linux-gnu"
> 
> # =========================================================================
> # COMPILER TUNING (CFLAGS / CXXFLAGS)
> # =========================================================================
> # -march=x86-64 : Guarantees binaries will execute on ANY 64-bit x86 CPU.
> # Frame Pointers: Restored (-fno-omit-frame-pointer) so when end-users 
> #                 experience crashes, the resulting core dumps yield 
> #                 readable stack traces for debugging.
> # =========================================================================
> CFLAGS="-march=x86-64 -mtune=generic -O2 -pipe -fno-plt -fexceptions \
>         -Wp,-D_FORTIFY_SOURCE=3 -Wformat -Werror=format-security \
>         -fstack-clash-protection -fcf-protection \
>         -fno-omit-frame-pointer -mno-omit-leaf-frame-pointer"
> CXXFLAGS="$CFLAGS -Wp,-D_GLIBCXX_ASSERTIONS"
> 
> # =========================================================================
> # LINKER & LOAD BALANCING (LDFLAGS / MAKEFLAGS)
> # =========================================================================
> # -fuse-ld=mold : Blazingly fast modern linker.
> # -l$(nproc)    : Prevents OOM kernel panics by stopping make from spawning
> #                 new jobs if system load exceeds physical core count.
> # =========================================================================
> LDFLAGS="-Wl,-O1 -Wl,--sort-common -Wl,--as-needed -Wl,-z,relro -Wl,-z,now \
>          -Wl,-z,pack-relative-relocs -fuse-ld=mold"
> LTOFLAGS="-flto=auto"
> MAKEFLAGS="-j$(nproc) -l$(nproc)"
> NINJAFLAGS="-j$(nproc)"
> 
> DEBUG_CFLAGS="-g"
> DEBUG_CXXFLAGS="$DEBUG_CFLAGS"
> RUSTFLAGS="-C link-arg=-fuse-ld=mold"
> 
> #########################################################################
> # BUILD ENVIRONMENT
> #########################################################################
> #
> BUILDENV=(!distcc color !ccache check !sign)
> 
> #########################################################################
> # GLOBAL PACKAGE OPTIONS
> #########################################################################
> #
> # =========================================================================
> # MASS DISTRIBUTION OPTIMIZATIONS (OPTIONS)
> # =========================================================================
> # autodeps : REQUIRED for mass distribution. Ensures makepkg injects 
> #            dynamic shared library (.so) requirements into the package
> #            metadata so pacman handles target dependencies correctly.
> # !debug   : Prevents generation of split debug packages, keeping the 
> #            final ISO payload light.
> # =========================================================================
> OPTIONS=(strip docs !libtool !staticlibs emptydirs zipman purge !debug lto autodeps)
> 
> INTEGRITY_CHECK=(sha256)
> STRIP_BINARIES="--strip-all"
> STRIP_SHARED="--strip-unneeded"
> STRIP_STATIC="--strip-debug"
> MAN_DIRS=({usr{,/local}{,/share},opt/*}/{man,info})
> DOC_DIRS=(usr/{,local/}{,share/}{doc,gtk-doc} opt/*/{doc,gtk-doc})
> PURGE_TARGETS=(usr/{,share}/info/dir .packlist *.pod)
> DBGSRCDIR="/usr/src/debug"
> LIB_DIRS=('lib:usr/lib' 'lib32:usr/lib32')
> 
> #########################################################################
> # COMPRESSION DEFAULTS
> #########################################################################
> #
> COMPRESSGZ=(gzip -c -f -n)
> COMPRESSBZ2=(bzip2 -c -f)
> COMPRESSXZ=(xz -c -z -)
> 
> # =========================================================================
> # COMPRESSION TUNING
> # =========================================================================
> # -T0 : Unlocks all available CPU threads for instantaneous zstd packaging.
> # =========================================================================
> COMPRESSZST=(zstd -c -T0 -)
> 
> COMPRESSLRZ=(lrzip -q)
> COMPRESSLZO=(lzop -q)
> COMPRESSZ=(compress -c -f)
> COMPRESSLZ4=(lz4 -q)
> COMPRESSLZ=(lzip -c -f)
> 
> #########################################################################
> # EXTENSION DEFAULTS
> #########################################################################
> #
> PKGEXT='.pkg.tar.zst'
> SRCEXT='.src.tar.gz'
> 
> # vim: set ft=sh ts=2 sw=2 et:
> ```