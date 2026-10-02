import { createTheme } from "@mui/material/styles";

export const theme = createTheme({
  palette: {
    primary: { main: "#087b83", dark: "#07546a", light: "#20bfc2", contrastText: "#ffffff" },
    secondary: { main: "#1157a7", dark: "#0a3d79", light: "#4e8ddd" },
    background: { default: "#eef6f8", paper: "#ffffff" },
    text: { primary: "#142f42", secondary: "#5d7482" },
    warning: { main: "#b86408" },
  },
  shape: { borderRadius: 16 },
  typography: {
    fontFamily: 'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    h3: { fontWeight: 800, letterSpacing: "-0.035em", lineHeight: 1.08 },
    h4: { fontWeight: 800, letterSpacing: "-0.028em", lineHeight: 1.12 },
    h5: { fontWeight: 750, letterSpacing: "-0.02em" },
    overline: { fontWeight: 800, letterSpacing: "0.13em", lineHeight: 1.5 },
    button: { textTransform: "none", fontWeight: 700 },
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: {
          backgroundColor: "#eef6f8",
          backgroundImage: "radial-gradient(circle at 88% 0%, rgba(32, 191, 194, 0.14), transparent 30rem)",
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: {
          border: "1px solid #d6e5e8",
          boxShadow: "0 12px 32px rgba(22, 75, 96, 0.07)",
        },
      },
    },
    MuiCard: {
      styleOverrides: {
        root: {
          border: "1px solid #d6e5e8",
          boxShadow: "0 10px 26px rgba(22, 75, 96, 0.055)",
        },
      },
    },
    MuiButton: {
      styleOverrides: {
        contained: { boxShadow: "0 9px 18px rgba(8, 123, 131, 0.22)" },
      },
    },
    MuiTextField: {
      styleOverrides: {
        root: {
          "& .MuiOutlinedInput-root": { backgroundColor: "rgba(255, 255, 255, 0.82)" },
        },
      },
    },
  },
});
