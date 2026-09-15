#!/usr/bin/env bash

# ⚠️ TODO WORK IN PROGRESS 🚧

{ # matching } is at the end of file, this allows logging

printf "Radiotomate install script starting at %s\n\n\n\n" "$(date)"

########################### PARAMETERS ################################################

# the pod, data folders, systemd service will use that name
: "${UNIT_NAME:=radiotomate}"

# radiotomate's target directory: this will be THE folder containing radio's data
: "${DATA_ROOT:=$PWD/${UNIT_NAME}_data}"

# Radiotomate live stream input configuration: you will have to configure your
# encoder to connect to radiotomate_host:$STREAM_INPUT_PORT/$STREAM_INPUT_NAME
: "${STREAM_INPUT_PORT:=6800}"
: "${STREAM_INPUT_NAME:=stream}"

# Radiotomate's web interface will be available at 0.0.0.0:$INTERFACE_PORT
: "${INTERFACE_PORT:=6811}"

# If set to 'yes', the script will configure a binding to the PulseAudio server
: "${USE_PULSEAUDIO:=no}"

# When set to 'yes', the script installs radiotomate as a pod in quadlet, that you can
# manage as a systemd service. Any value other than 'yes' will only write the Kubernetes
# yaml manifest for the services, which is suitable if you want to use your own
# containers manager.
: "${INSTALL_QUADLET:=yes}"

# if you want to reuse an existing beets library, BEETSDIR is the path to the folder containing config.yaml (see `beet config -p`).
# we also expect its database to be in $BEETSDIR/library.db (this is beets' default)
# NOTE: beet's config.yaml should not contain paths prefixed with ~, always use absolute paths!
: "${BEETSDIR:=$DATA_ROOT/Beets}"

# Path to the music directory, aka Beets' "directory" parameter.
# when re-using a beets library, 
: "${MUSIC_DIRECTORY:=$DATA_ROOT/Music}"

# Path to the music "dropbox" directory, aka drop2beets' "dropbox_path" parameter.
# Files copied/moved to this folder will be automatically moved to the music collection.
: "${MUSIC_DROPBOX:=$DATA_ROOT/MusicDropbox}"

# if you don't want to enter the admin password interactively, set this variable
: "${ADMIN_PASSWORD:=}"

# any value other than 'yes' will skip the confirmation
: "${ASK_FOR_CONFIRMATION:=yes}"

# set to 1 to have more traces from the installer
: "${DEBUG:=0}"

# OCI image name for the playout application
: "${PLAYOUT_IMAGE:=localhost/radiotomate/playout:latest}"

# OCI image name for web applications
: "${WEBAPPS_IMAGE:=localhost/radiotomate/webapps:latest}"

# parameters above are sorted from the most common to the most development-oriented
#######################################################################################

printf "We computed the following parameters (this can be useful when re-installing):\n"
for v in UNIT_NAME DATA_ROOT STREAM_INPUT_PORT STREAM_INPUT_NAME INTERFACE_PORT USE_PULSEAUDIO INSTALL_QUADLET BEETSDIR MUSIC_DIRECTORY MUSIC_DROPBOX ASK_FOR_CONFIRMATION DEBUG PLAYOUT_IMAGE WEBAPPS_IMAGE
do
  printf "%s=\"%s\" " "$v" "${!v}"
done
printf "\n"

set -o errexit
set -o nounset
set -o pipefail
if [[ "$DEBUG" == "1" ]]
then
    set -o xtrace
fi

NEWLINE=$'\n'
BOLD=$(tput bold)

BASH_PROFILE=""

BEETSCONFIG="$BEETSDIR/config.yaml" # this file name is hardcoded as CONFIG_FILENAME in the confuse module
if [ -f "$BEETSCONFIG" ]
then
    BEETSCONFIGVERB="re-use"
else
    BEETSCONFIGVERB="create"
fi

URL="http://localhost:$INTERFACE_PORT"

TODOLIST=" * download Radiotomate's container images ($WEBAPPS_IMAGE and $PLAYOUT_IMAGE)
 * create Radiotomate configuration files and data in $DATA_ROOT
 * $BEETSCONFIGVERB the Beets music library configured in $BEETSCONFIG
 * listen for incoming streams on 0.0.0.0:$STREAM_INPUT_PORT/$STREAM_INPUT_NAME
 * make Radiotomate available at $URL"

KUBE_MANIFEST="$HOME/.config/containers/systemd/${UNIT_NAME}_pod_manifest.yaml"
SERVICE_FILE="$HOME/.config/containers/systemd/${UNIT_NAME}.kube"


check () {
    if [[ ! "$(which podman)" ]]
    then
        TODOLIST=" * install the container engine podman (using sudo apt install podman)$NEWLINE$TODOLIST"
    fi
    if [[ "$INSTALL_QUADLET" != "yes" ]]
    then
        KUBE_MANIFEST="$(pwd)/${UNIT_NAME}_kube.yaml"
        TODOLIST=" * write $KUBE_MANIFEST (but will not run it)$NEWLINE$TODOLIST"
    fi
    if [[ -f "$HOME/.bashrc" ]]
    then
        BASH_PROFILE="$HOME/.bashrc"
    elif [[ -f "$HOME/.bash_profile" ]]
    then
        BASH_PROFILE="$HOME/.bash_profile"
    fi
    if [[ "$BASH_PROFILE" != "" ]]
    then
        TODOLIST="$TODOLIST$NEWLINE * Register 'beet' and 'radiotomate' commands in $BASH_PROFILE"
    fi
}


intro () {
    printf "We are about to:\n%s\n\n" "$TODOLIST"
    if [[ "$ASK_FOR_CONFIRMATION" == "yes" ]]
    then
        read -r -p "If this is what you want to do, please type 'go' and Enter: " choice
        case "$choice" in
            go ) echo "" ;;
            * ) exit 1;;
        esac
    fi

    if [[ "$USE_PULSEAUDIO" == "yes" ]]
    then
        PULSEAUDIO_PATH="/run/user/$(id -u)/pulse/native"
        if [[ ! -S "$PULSEAUDIO_PATH" ]]
        then
            printf "\n\n! We were asked to use pulseaudio, but %s is not a unix socket.\n" "$PULSEAUDIO_PATH"
            exit 2
        fi
    fi
}


install_radiotomate() {
    if [[ "$(which podman)" ]]
    then
        printf "(podman already available, skipping its installation)\n"
    else
        printf "Trying to install podman - you might have to enter your password\n"
        DEBIAN_FRONTEND=noninteractive sudo apt install -y podman
    fi

    case "$PLAYOUT_IMAGE" in
      localhost*)
        true;;
      *)
        podman pull "$PLAYOUT_IMAGE";;
    esac

    case "$WEBAPPS_IMAGE" in
      localhost*)
        true;;
      *)
        podman pull "$WEBAPPS_IMAGE";;
    esac

    mkdir -p "$DATA_ROOT"
    mkdir -p "$MUSIC_DIRECTORY"
    mkdir -p "$MUSIC_DROPBOX"
    mkdir -p "$BEETSDIR"

    if [[ "$INSTALL_QUADLET" == "yes" ]]
    then
      mkdir -p "$(dirname "$KUBE_MANIFEST")"
    fi

    if [ -f "$BEETSCONFIG" ]
    then
        printf "Re-using Beets configuration file: %s\n" "$BEETSCONFIG"
    else
        cat > "$BEETSCONFIG" << EOF
# this is beets configuration for $UNIT_NAME
# it has been generated by Radiotomate and impacts the radio's auto-DJ and music
# collection, so edit with care, try on a test machine first, and don't use ~

directory: $MUSIC_DIRECTORY
library: $BEETSDIR/library.db
import:
    move: yes
    copy: no
    quiet_fallback: asis
    write: yes
plugins: info random replaygain drop2beets
replaygain:
    backend: ffmpeg
    overwrite: yes
drop2beets:
    dropbox_path: $MUSIC_DROPBOX
    log_path: $BEETSDIR/dropbox.log
    on_item: |
        def on_item(item, path):
            """
            The default implementation checks that the incoming file has non-empty
            "Artist" and "Title" tags, because it would not look correct in radio's
            metadata.

            Parameters:
                item: the beets Item that we're about to import
                path: its sub-folders path in our dropbox ; if the items has been dropped at the root, then it's empty.
            Returns:
                A dict of custom attributes (according to path, maybe) ; return None if you don't want to import the file right now.
            """

            if not item.artist.strip() or not item.title.strip():
                _logger.error("Missing artist or title tag")

                # when returning None, the file is left in the dropbox
                return None

            return item

EOF

    fi

    RTCONFIG=$DATA_ROOT/radiotomate.yaml

    if [[ "$USE_PULSEAUDIO" == "yes" ]]
    then
        SECURITY_CONTEXT="
      - name: PULSE_SERVER
        value: "unix:$PULSEAUDIO_PATH"
    securityContext:
      seLinuxOptions:
        type: spc_t
"
        PULSEAUDIO_MOUNT="
      - mountPath: $PULSEAUDIO_PATH
        name: pulseaudio
"
        PULSEAUDIO_VOLUME="
    - name: pulseaudio
      hostPath:
        path: $PULSEAUDIO_PATH
"
    else
        SECURITY_CONTEXT=""
        PULSEAUDIO_MOUNT=""
        PULSEAUDIO_VOLUME=""
    fi

    # notes on the Kube manifest:
    # - we bind the host's timezone to ensure that containers are on the same time
    # - podman annotation is equivalent to --userns=keep-id, so containers read/write files like the running user
    cat > "$KUBE_MANIFEST" << EOF
---
apiVersion: v1
kind: Pod
metadata:
  annotations:
    io.podman.annotations.userns: keep-id
  name: $UNIT_NAME
spec:
  containers:
  - name: playout
    image: $PLAYOUT_IMAGE
    command:
      - liquidsoap
      - /app/radiotomate.liq
    env:
      - name: RTCONFIG
        value: $RTCONFIG
$SECURITY_CONTEXT
    volumeMounts:
      - mountPath: $DATA_ROOT:z
        name: data-volume
      - mountPath: $MUSIC_DIRECTORY:z
        name: beetsmusic
      - mountPath: /etc/localtime:z
        name: tz-config
$PULSEAUDIO_MOUNT
    ports:
      - containerPort: 6800
        hostPort: $STREAM_INPUT_PORT
  - name: interface
    image: $WEBAPPS_IMAGE
    command:
      - radiotomate
      - interface
    env:
      - name: BEETSDIR
        value: $BEETSDIR
      - name: RTCONFIG
        value: $RTCONFIG
    volumeMounts:
      - mountPath: $DATA_ROOT:z
        name: data-volume
      - mountPath: $BEETSDIR:z
        name: beetsdir
      - mountPath: $MUSIC_DIRECTORY:z
        name: beetsmusic
      - mountPath: /etc/localtime:z
        name: tz-config
    ports:
      - containerPort: 6811
        hostPort: $INTERFACE_PORT
  - name: scheduler
    image: $WEBAPPS_IMAGE
    command:
      - radiotomate
      - scheduler
    env:
      - name: BEETSDIR
        value: $BEETSDIR
      - name: RTCONFIG
        value: $RTCONFIG
    volumeMounts:
      - mountPath: $DATA_ROOT:z
        name: data-volume
      - mountPath: $BEETSDIR:z
        name: beetsdir
      - mountPath: $MUSIC_DIRECTORY:z
        name: beetsmusic
      - mountPath: /etc/localtime:z
        name: tz-config
  - name: dropbox
    image: $WEBAPPS_IMAGE
    command:
      - beet
      - dropbox
    env:
      - name: BEETSDIR
        value: $BEETSDIR
    volumeMounts:
      - mountPath: $BEETSDIR:z
        name: beetsdir
      - mountPath: $MUSIC_DIRECTORY:z
        name: beetsmusic
      - mountPath: $MUSIC_DROPBOX:z
        name: dropbox
      - mountPath: /etc/localtime:z
        name: tz-config
  volumes:
    - name: data-volume
      hostPath:
        path: $DATA_ROOT
    - name: beetsdir
      hostPath:
        path: $BEETSDIR
    - name: beetsmusic
      hostPath:
        path: $MUSIC_DIRECTORY
    - name: dropbox
      hostpath:
        path: $MUSIC_DROPBOX
    - name: tz-config
      hostPath:
        path: /etc/localtime
        type: File
$PULSEAUDIO_VOLUME

EOF

    # FIXME we force fuse-overlayfs because otherwise container creation is very slow
    # on some systems. Note that we also force it in the quadlet definition. cf:
    #  * https://github.com/containers/podman/blob/main/docs/tutorials/performance.md#choosing-a-storage-driver
    #  * https://github.com/containers/podman/issues/16541
    PODMAN_RUN=(podman --storage-driver=overlay --storage-opt overlay.mount_program=/usr/bin/fuse-overlayfs run --rm -ti --userns=keep-id -e "BEETSDIR=$BEETSDIR" -v "$BEETSDIR:$BEETSDIR:z" -v "$MUSIC_DIRECTORY:$MUSIC_DIRECTORY:z" -v "$DATA_ROOT:$DATA_ROOT:z")

    # because of the bug above, we must manually create a container for both image
    # before ever starting the pod
    printf "Checking playout installation:\n"
    "${PODMAN_RUN[@]}" --entrypoint /usr/bin/liquidsoap "$PLAYOUT_IMAGE" --version

    if [[ "$BASH_PROFILE" != "" ]]
    then
        sed -i '/^alias beet=/d' "$BASH_PROFILE"
        echo "alias beet='${PODMAN_RUN[*]} $WEBAPPS_IMAGE beet'" >> "$BASH_PROFILE"

        sed -i '/^alias radiotomate=/d' "$BASH_PROFILE"
        echo "alias radiotomate=\"${PODMAN_RUN[*]} $WEBAPPS_IMAGE radiotomate --config-path ${RTCONFIG@Q}\"" >> "$BASH_PROFILE"
    fi

    "${PODMAN_RUN[@]}" "$WEBAPPS_IMAGE" radiotomate install -d "$DATA_ROOT"

    if [[ "$ADMIN_PASSWORD" != "" ]]
    then
        printf "The first user will be 'admin', using the value provided in ADMIN_PASSWORD\n"
        "${PODMAN_RUN[@]}" "$WEBAPPS_IMAGE" radiotomate --config-path "$RTCONFIG" useradd admin --admin --password "$ADMIN_PASSWORD"
    else
        printf "\n\nThe first user will be 'admin', please type its password:\n"
        "${PODMAN_RUN[@]}" "$WEBAPPS_IMAGE" radiotomate --config-path "$RTCONFIG" useradd admin --admin
    fi
}


install_quadlet () {
    # about GlobalArgs, see: $PODMAN_RUN
    cat > "$SERVICE_FILE" << EOF
[Install]
WantedBy=default.target

[Kube]
Yaml=$KUBE_MANIFEST
PublishPort=$STREAM_INPUT_PORT:6800
PublishPort=$INTERFACE_PORT:6811
GlobalArgs=--storage-driver=overlay --storage-opt overlay.mount_program=/usr/bin/fuse-overlayfs
EOF

    systemctl --user daemon-reload
    printf "Starting %s.service...\n" "$UNIT_NAME"
    systemctl --user start "$UNIT_NAME.service"
    loginctl enable-linger
}


outro () {
    printf "\n%s %s is installed\n\n" "$BOLD" "$UNIT_NAME"
    printf "Actions performed:\n"
    printf "%s\n\n" "$TODOLIST"
    printf "Edit %s/radiotomate.yaml to configure outputs, then restart Radiotomate.\n" "$DATA_ROOT"
    if [[ "$INSTALL_QUADLET" == "yes" ]]
    then
        printf "(to restart Radiotomate, type:\n    systemctl --user start %s.service\n)\n" "$UNIT_NAME"
    else
        printf "You may start Radiotomate with:\n    podman play kube %s --replace\n" "$KUBE_MANIFEST"
    fi
    printf "Go to %s to push content for this new radio!\n" "$URL"
    printf "If something goes wrong, look at the end of .log files in %s\n" "$DATA_ROOT"
}


check
intro
install_radiotomate
if [[ "$INSTALL_QUADLET" == "yes" ]]
then
    install_quadlet
fi
outro

} 2>&1 | tee -a radiotomate_install.log
