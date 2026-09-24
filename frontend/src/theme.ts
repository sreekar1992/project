import { createTheme } from "@mui/material/styles";

export const theme = createTheme({
  palette: {
    primary: { main: "#0b5d4c", dark: "#063d32", light: "#398371" },
    secondary: { main: "#466274" },
    background: { default: "#f5f8f7", paper: "#ffffff" },
    warning: { main: "#a85b00" },
  },
  shape: { borderRadius: 12 },
  typography: {
    fontFamily: 'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    h4: { fontWeight: 750, letterSpacing: "-0.025em" },
    h5: { fontWeight: 700 },
    button: { textTransform: "none", fontWeight: 700 },
  },
  components: {
    MuiPaper: {
      styleOverrides: { root: { border: "1px solid #dce7e3", boxShadow: "none" } },
    },
  },
});
