#!/usr/bin/fish

if status is-interactive
    # Source user-specific config if it exists
    if test -f ~/.fish.config.user
        source ~/.fish.config.user
    end
end
