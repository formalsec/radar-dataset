{ pkgs ? import <nixpkgs> { } }:

let
  python = pkgs.python3.withPackages (ps: with ps; [
    requests # crawler + toolkit GitHub access
    openai # crawler/src/gpt.py
    yaspin # crawler/src/gpt.py spinner
    matplotlib # crawler/src/venn_packages,py
    matplotlib-venn
    tree-sitter # JS/TS AST parsing in toolkit/src/js_ast.py
    tree-sitter-javascript
    tree-sitter-typescript
  ]);
in
pkgs.mkShell {
  packages = [
    python
    pkgs.git
  ];

  shellHook = ''
    # Load secrets from .env (gitignored; template in .env.example).
    if [ -f ${toString ./.}/.env ]; then
      while IFS='=' read -r key value || [ -n "$key" ]; do
        case "$key" in ""|\#*) continue ;; esac
        [ -n "$value" ] && export "$key=$value"
      done < ${toString ./.}/.env
    fi

    if [ -z "$GITHUB_TOKEN" ]; then
      echo "note: GITHUB_TOKEN is not set (unauthenticated GitHub API is limited to ~60 req/h)"
    fi
  '';
}
