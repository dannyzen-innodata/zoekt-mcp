// zoekt-mcp docs. Strict: any broken link, markdown link, or anchor fails the build.
const repo = "https://github.com/dannyzen-innodata/zoekt-mcp";

export default {
  title: "zoekt-mcp",
  tagline: "M365-Authenticated Sourcegraph Zoekt Code Search MCP Server",
  url: "http://127.0.0.1:3000",
  baseUrl: "/",
  onBrokenLinks: "throw",
  onBrokenAnchors: "throw",
  markdown: {
    format: "detect",
    hooks: { onBrokenMarkdownLinks: "throw" },
  },
  presets: [
    [
      "classic",
      {
        docs: {
          path: "content",
          routeBasePath: "/",
          sidebarPath: "./sidebars.mjs",
          editUrl: `${repo}/edit/main/docs/`,
        },
        blog: false,
      },
    ],
  ],
  themeConfig: {
    navbar: {
      title: "zoekt-mcp",
      items: [{ href: repo, label: "GitHub", position: "right" }],
    },
    colorMode: { respectPrefersColorScheme: true },
  },
};
