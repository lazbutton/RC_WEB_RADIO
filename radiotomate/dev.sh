#!/bin/bash

# Starts a tmux session with one panel for each of the 3 radiotomate processes, in verbose and restart mode

export TMUXSESSION=radiotomatetmux
export RTCONFIG=radio_data/radiotomate.yaml

case "$SHELL" in
    *fish )
        INTRO="eval (poetry env activate) "
        ;;
    *)
        INTRO="eval \$(poetry env activate) "
        ;;
esac


tmux kill-session -t $TMUXSESSION
tmux new-session -d -s $TMUXSESSION \; \
    send-keys "AUTORELOAD=y liquidsoap playout/radiotomate.liq" Enter \; \
    split-window -v -p33 \; \
    send-keys "$INTRO" Enter \; \
    send-keys "radiotomate --verbose scheduler --reload" Enter \; \
    split-window -v -p33 \; \
    send-keys "$INTRO" Enter \; \
    send-keys " radiotomate --verbose interface --reload" Enter \; \
    rename-window radiotomate \; \
    select-layout even-vertical \; \
    attach
