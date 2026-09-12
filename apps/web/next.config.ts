import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Local browser acceptance may use either host spelling. This only affects
  // Next development resources; API CORS remains configured by FastAPI.
  allowedDevOrigins: ["localhost", "127.0.0.1"],
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/api/:path*",
      },
      {
        source: "/ws/:path*",
        destination: "http://localhost:8000/ws/:path*",
      },
    ];
  },
};

export default nextConfig;
