> [!NOTE]+ Channel (qemu-ga)
> ```ini
> <channel type="unix">
>   <target type="virtio" name="org.qemu.guest_agent.0"/>
>   <address type="virtio-serial" controller="0" bus="0" port="1"/>
> </channel>
> ```

> [!NOTE]+ Channel (spice)
> ```ini
> <channel type="spicevmc">
>   <target type="virtio" name="com.redhat.spice.0"/>
>   <address type="virtio-serial" controller="0" bus="0" port="2"/>
> </channel>
> ```

> [!NOTE]+ Display Spice
> ```ini
> <graphics type="spice">
>   <listen type="none"/>
>   <image compression="off"/>
>   <gl enable="yes" rendernode="/dev/dri/renderD128"/>
> </graphics>
> ```

> [!NOTE]+ Video Virtio
> ```ini
> <video>
>   <model type="virtio" heads="1" primary="yes">
>     <acceleration accel3d="yes"/>
>   </model>
>   <address type="pci" domain="0x0000" bus="0x00" slot="0x01" function="0x0"/>
> </video>
> ```


> [!NOTE]+ bridged network name
> ```ini
> virbr0
> ```