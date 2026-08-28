import { defineConfig } from "@hey-api/openapi-ts";

const input = process.env.OPENAPI_TS_INPUT ?? "../../../openapi.json";
const output = process.env.OPENAPI_TS_OUTPUT ?? "src/generated";

export default defineConfig({
  input,
  output: {
    path: output,
    header: "// @generated",
  },
  plugins: [
    {
      name: "@hey-api/typescript",
      enums: "typescript",
    },
    {
      name: "@hey-api/sdk",
      client: "@hey-api/client-fetch",
      operations: {
        strategy: "flat",
      },
    },
  ],
});
