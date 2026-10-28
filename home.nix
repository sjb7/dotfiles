{ config, lib, pkgs, user, ... }:

let
  dotfiles = "${config.home.homeDirectory}/.dotfiles";
  skillsDir = ./home/skills;
  skillNames =
    if builtins.pathExists skillsDir
    then builtins.attrNames (
      lib.filterAttrs (_: type: type == "directory") (builtins.readDir skillsDir)
    )
    else [];
  agentSkillDirs = [
    ".agents/skills"
    ".gemini/config/skills"
    ".claude/skills"
    ".codex/skills"
    ".grok/skills"
    ".cursor/skills"
    ".config/opencode/skills"
    ".pi/agent/skills"
    ".hermes/skills/custom"
  ];
  skillLinks = builtins.listToAttrs (
    lib.concatMap (skill:
      map (dir: {
        name = "${dir}/${skill}";
        value = {
          source = config.lib.file.mkOutOfStoreSymlink "${dotfiles}/home/skills/${skill}";
        };
      }) agentSkillDirs
    ) skillNames
  );
in

{
  home.username = user;
  home.homeDirectory = "/Users/${user}";
  home.stateVersion = "24.11";
  home.packages = with pkgs; [
    # cli i use constantly
    ripgrep   # fast search
    fd        # fast find
    fzf       # fuzzy finder
    jq        # json on the command line
    lazygit
    neovim
    # the font everything renders in
    nerd-fonts.hack
  ];
  fonts.fontconfig.enable = true;
  home.sessionVariables.EDITOR = "nvim";
  home.sessionPath = [ "$HOME/.local/bin" ];

  programs.zsh = {
    enable = true;
    autosuggestion.enable = true;      # ghost text from history
    syntaxHighlighting.enable = true;  # commands turn green when valid
    initContent = ''
      bindkey '^f' autosuggest-accept
    '';
    shellAliases = {
      ".." = "cd ..";
      add = "git add .";
      push = "git push";
      pull = "git pull";
      m = "git switch main";
      cc = "claude --dangerously-skip-permissions";
      co = "codex --full-auto";
    };
  };

  programs.starship = {
    enable = true;
    settings = {
      add_newline = false;
      format = "$directory$git_branch$git_status$cmd_duration$line_break$character";
      character = {
        success_symbol = "[❯](purple)";
        error_symbol = "[❯](red)";
      };
      cmd_duration.format = "[$duration]($style) ";
    };
  };

  programs.git = {
    enable = true;
    settings.user = {
      name = "Shubham Jain";
      email = "sj95.b7@gmail.com";
    };
  };

  # Edit-in-place: the real file stays in my repo, ~/.config just points at it.
  home.file = skillLinks // {
    ".config/wezterm".source =
      config.lib.file.mkOutOfStoreSymlink "${dotfiles}/home/.config/wezterm";
    ".config/nvim".source =
      config.lib.file.mkOutOfStoreSymlink "${dotfiles}/home/.config/nvim";
    ".config/herdr".source =
      config.lib.file.mkOutOfStoreSymlink "${dotfiles}/home/.config/herdr";
    ".claude/settings.json".source =
      config.lib.file.mkOutOfStoreSymlink "${dotfiles}/home/.claude/settings.json";

    # Keep Pi's credential and runtime state local by linking only authored files and directories.
    ".pi/agent/themes".source =
      config.lib.file.mkOutOfStoreSymlink "${dotfiles}/home/.pi/agent/themes";
    ".pi/agent/extensions".source =
      config.lib.file.mkOutOfStoreSymlink "${dotfiles}/home/.pi/agent/extensions";
    ".pi/agent/models.json".source =
      config.lib.file.mkOutOfStoreSymlink "${dotfiles}/home/.pi/agent/models.json";
    ".pi/agent/settings.json".source =
      config.lib.file.mkOutOfStoreSymlink "${dotfiles}/home/.pi/agent/settings.json";

    ".claude/CLAUDE.md".source =
      config.lib.file.mkOutOfStoreSymlink "${dotfiles}/home/AGENTS.md";
    ".codex/AGENTS.md".source =
      config.lib.file.mkOutOfStoreSymlink "${dotfiles}/home/AGENTS.md";
    ".config/opencode/AGENTS.md".source =
      config.lib.file.mkOutOfStoreSymlink "${dotfiles}/home/AGENTS.md";

    ".gemini/statusline.py".source =
      config.lib.file.mkOutOfStoreSymlink "${dotfiles}/home/statusline.py";
    ".gemini/antigravity-cli/statusline.py".source =
      config.lib.file.mkOutOfStoreSymlink "${dotfiles}/home/statusline.py";
    ".claude/statusline.py".source =
      config.lib.file.mkOutOfStoreSymlink "${dotfiles}/home/statusline.py";
    ".grok/statusline.py".source =
      config.lib.file.mkOutOfStoreSymlink "${dotfiles}/home/statusline.py";
  };

  home.activation.setupAgentTools = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    (
      export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

      if ! command -v treehouse >/dev/null 2>&1; then
        $DRY_RUN_CMD /usr/bin/curl -fsSL https://kunchenguid.github.io/treehouse/install.sh | $DRY_RUN_CMD /bin/sh
      fi

      if command -v npm >/dev/null 2>&1; then
        if ! command -v lavish-axi >/dev/null 2>&1; then
          $DRY_RUN_CMD npm install -g lavish-axi
        fi
        $DRY_RUN_CMD lavish-axi setup hooks >/dev/null 2>&1 || true
        $DRY_RUN_CMD lavish-axi setup plugin >/dev/null 2>&1 || true
        if [ ! -d "$HOME/.agents/skills/lavish" ]; then
          $DRY_RUN_CMD npx -y skills add kunchenguid/lavish-axi --skill lavish -g -y >/dev/null 2>&1 || true
        fi
      fi
    )
  '';

  home.activation.setupHerdrIntegrations = lib.hm.dag.entryAfter [ "writeBoundary" "setupAgentTools" ] ''
    (
      export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

      if command -v herdr >/dev/null 2>&1; then
        for agent in pi claude codex cursor grok antigravity-cli opencode hermes; do
          $DRY_RUN_CMD herdr integration install "$agent" >/dev/null 2>&1 || true
        done

        installed_plugins="$(herdr plugin list 2>/dev/null || true)"
        for plugin in \
          "zenbu-labs/terminal-code/herdr-plugin"; do
          if ! printf '%s\n' "$installed_plugins" | grep -qF "github:$plugin@"; then
            $DRY_RUN_CMD herdr plugin install --yes "$plugin" >/dev/null 2>&1 || true
          fi
        done
      fi
    )
  '';
}
