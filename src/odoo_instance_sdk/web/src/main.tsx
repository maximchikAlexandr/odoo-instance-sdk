import React from "react";
import ReactDOM from "react-dom/client";
import { MantineProvider, createTheme } from "@mantine/core";
import "@mantine/core/styles.css";
import "./monitor.css";
import App from "./App";

const theme = createTheme({
  primaryColor: "odoo",
  primaryShade: 6,
  fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Ubuntu, "Noto Sans", Arial, sans-serif',
  defaultRadius: 4,
  colors: {
    odoo: ["#f6f0f4", "#ecdee7", "#d9bfce", "#c09ab1", "#a97c98", "#8e617f", "#714b67", "#61405a", "#52364c", "#452d40"],
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <MantineProvider theme={theme}>
      <App />
    </MantineProvider>
  </React.StrictMode>,
);
