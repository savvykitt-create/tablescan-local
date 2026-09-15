# Printable Excel forms

The Von Frey, Plantar and Staircase workbooks start with 20 animals. ID numbering
uses formulas; L and R headers have distinct colors, and Notes provides space for
free text. Date, Session and Operator are outside the measurement grid.

## Change the number of animals

Save your own copy. Insert or delete complete worksheet data rows **inside the
table**, preserving ID formulas, borders and uniform row heights. Keep the first
six worksheet rows, column order and column widths. Clearing cells does not
remove grid rows. Do not merge measurement cells.

The same protocol supports arbitrary counts within 200 total grid rows (199
animals with one header). The number is detected from the scan, not chosen from
a fixed list. Increasing the count reduces printed row height when the form is
scaled to one page; use sufficient paper size and scan resolution for legibility.

## Print, scan and recognize

1. Check print preview: landscape A4 is the default, with 10 mm margins and one
   page in each direction. All metadata and outer borders must be visible.
2. Fill the printed form by hand. Blank measurements mean missing values, not zero.
3. Scan the complete page. In TableScan Local, select the matching built-in
   protocol, or import its JSON from the `templates` folder.
4. Inspect the fitted grid and metadata positions. Batch preparation checks each
   file's rotation and fit and allows individual corrections before analysis.
5. Review recognized values, especially decimal separators and free text.
6. Export a new workbook. Compact contains original table layouts and **Fields**;
   Extended includes normalized **Data** and detailed **Audit** alongside layouts.

Individual manual corrections can override recognition constraints. Different
page layouts or row counts within one PDF require separate analysis jobs.

| Form | Measurement columns |
| --- | --- |
| Von Frey | L1–L5 and R1–R5; nonnegative numbers |
| Plantar | L1–L5 and R1–R5; nonnegative numbers |
| Staircase | L1–L7, L drop, R1–R7, R drop; nonnegative integers |

Cell comments explain default rules but do not print. The blank source workbook
is reusable; export creates a separate workbook with the recognized results.
