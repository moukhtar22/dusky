iso maker:
~/user_scripts/arch_iso_scripts/offline_iso/iso_maker/python/dusky_iso_generator.py

iso installer pre chroot (pacstrap)
~/user_scripts/arch_iso_scripts/offline_iso/070_pacstrap_and_disable_mkinitcpio.py

iso installer chroot
    pacman:
    ~/user_scripts/arch_iso_scripts/offline_iso/130_chroot_package_installer.sh
    aur:
    ~/user_scripts/arch_iso_scripts/offline_iso/131_chroot_aur_packages.sh

user space:
    old pacman:
    ~/user_scripts/arch_setup_scripts/scripts/060_package_installation.sh

    old aur:
    ~/user_scripts/arch_setup_scripts/scripts/100_paru_packages.sh

    new:
    ~/user_scripts/arch_setup_scripts/scripts/060_package_installation.py
        pacman:
        ~/user_scripts/arch_setup_scripts/scripts/package_profiles/01_all
        aur:
        ~/user_scripts/arch_setup_scripts/scripts/package_profiles/aur/01_all


    updater:
        pacman:
        ~/user_scripts/arch_setup_scripts/scripts/package_profiles/updater_packages
        aur
        ~/user_scripts/arch_setup_scripts/scripts/package_profiles/aur/updater_packages

