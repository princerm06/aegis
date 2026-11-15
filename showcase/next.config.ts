import type { NextConfig } from "next";

const config: NextConfig = {
  output: "export",
  devIndicators: false,
  poweredByHeader: false,
  productionBrowserSourceMaps: false,
};

export default config;
