# MITS ECG manuscript in LaTeX

`mits_ecg_jpp.tex` converts the supplied ECG manuscript into the structure of the supplied Journal of Plasma Physics LaTeX example. It includes the MITS name and the logo cropped from the image supplied with the request. All six tables, four figures, two equations, and seven references are included.

The source needs `jpp.cls`, which was not included in the pasted template. Get the official LaTeX template ZIP from the [Cambridge JPP author instructions](https://www.cambridge.org/core/journals/journal-of-plasma-physics/information/author-instructions/preparing-your-materials) and place `jpp.cls` beside `mits_ecg_jpp.tex`. Then compile with a LaTeX installation, for example:

```sh
pdflatex mits_ecg_jpp.tex
pdflatex mits_ecg_jpp.tex
```

The project environment did not have a LaTeX compiler, so a PDF render could not be checked here. The class file is a journal style dependency; using it does not imply that this ECG article is in that journal's subject scope.
