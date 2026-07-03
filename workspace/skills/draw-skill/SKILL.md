---
name: draw-skill
description: Generate publication-ready, black-and-white draw.io (.drawio) software engineering diagrams for academic papers, including flowcharts, UML use case diagrams, UML activity diagrams, sequence diagrams, ER diagrams, UML class diagrams, UML state diagrams, C4/UML architecture diagrams, and UML/C4 deployment diagrams. Also export .drawio files to PNG, JPG, SVG, or PDF when requested. Use when Codex is asked to create, revise, standardize, validate, or export software engineering diagrams for papers, theses, dissertations, reports, or formal technical documentation.
---

# Academic draw.io software engineering diagrams

## Mandatory workflow

1. Identify the requested operation: create/revise a diagram, validate an existing `.drawio`, or export an existing `.drawio` to an image/document format.
2. If the user only asks to export an existing `.drawio`, use the export workflow below. Do not redraw or modify the source file unless explicitly requested.
3. Identify the requested diagram type when drawing or revising: `flowchart`, `usecase`, `activity`, `sequence`, `er`, `class`, `state`, `architecture`, or `deployment`.
4. Read `references/绘图规范/00-总则-论文软件工程图绘制规范.md`.
5. Read the matching file in `references/绘图规范/` before drawing:
   - Flowchart: `01-流程图绘制规范.md`
   - Use case: `08-用例图绘制规范.md`
   - Activity: `09-活动图绘制规范.md`
   - Sequence: `02-时序图绘制规范.md`
   - ER: `03-ER图绘制规范.md`
   - Class: `04-类图绘制规范.md`
   - State: `05-状态图绘制规范.md`
   - Architecture: `06-架构图绘制规范.md`
   - Deployment: `07-部署图绘制规范.md`
6. If the request is ambiguous, choose the narrowest standard diagram that answers the paper's claim. Do not merge diagram types unless the user explicitly asks for a hybrid figure.
7. Generate a `.drawio` file, not an image, unless the user explicitly asks for an export. The file must open directly in draw.io / diagrams.net.
8. Validate the file as XML and inspect the style strings for the publication profile below.

## Gateway workspace output rules

- Always write generated `.drawio` files and exported images/documents under `workspace/reports/diagrams/`.
- Never write final diagram artifacts to `/tmp`, a container-local private path, or a worker-specific directory. In Docker multi-worker mode, `/app/workspace` is the shared bind mount visible to API, worker, delivery, scheduler, and dashboard roles.
- Use `workspace/...` relative paths in tool calls and final answers. Do not use host absolute paths such as `/home/.../gateway/...`; Docker workers run under `/app`, and the gateway tool layer maps `workspace/...` to the correct shared workspace.
- Use stable relative paths in the answer, for example `workspace/reports/diagrams/网关架构图.drawio` and `workspace/reports/diagrams/网关架构图.png`.
- If the user wants the diagram sent through Feishu, include the exported file path in the final answer or in delivery metadata. Feishu upload only sends files inside `workspace/reports/`, so `workspace/reports/diagrams/` is the required location.
- Prefer Chinese file names when the user requests Chinese output. Keep extensions lowercase: `.drawio`, `.png`, `.svg`, `.pdf`, `.jpg`.
- If `.drawio` has been generated and PNG/PDF export fails because an optional renderer is missing, stop retrying and return the `.drawio` path plus any available `.svg` path. Do not run `apt-get`, install desktop packages, or repeatedly probe renderers inside the agent turn.

## Publication profile

Use these settings unless the user gives a stricter journal or university template:

- Color: black and white only. Use `strokeColor=#000000`, `fontColor=#000000`, and `fillColor=#ffffff` or no fill. Do not use colorful semantic fills.
- Font: `fontFamily=SimSun` for every text-bearing cell. This corresponds to 宋体.
- Size: `fontSize=12` for normal labels, approximately Chinese 小四 in draw.io output. Use `fontSize=14` only for an optional title and `fontSize=10` for secondary annotations.
- Lines: `strokeWidth=1` for normal nodes and connectors. Use `strokeWidth=1.2` only for outer boundaries.
- Geometry: prefer a wide and shallow composition for paper figures. Lay out diagrams horizontally whenever the notation allows it, minimizing vertical height while preserving reading order and avoiding crossings.
- Node fit: size boxes close to their labels. Avoid large empty areas inside rectangles, diamonds, swimlanes, and boundary boxes. Use compact padding and dimensions based on text length.
- Connectors: prefer orthogonal connectors for flow, ER, class, architecture, and deployment diagrams. Sequence diagrams may use horizontal message lines.
- Output: default to A4 landscape (`1169 x 827`) for academic paper figures. Use portrait only when the user explicitly asks or the target notation is genuinely vertical.

## Layout rules for paper figures

- Prefer left-to-right layout over top-to-bottom layout for flowcharts, state diagrams, ER diagrams, class diagrams, architecture diagrams, and deployment diagrams.
- Keep the final diagram's visual bounding box wide and short. As a practical target, aim for width at least 2.5 times height for simple flow/state/architecture figures, and avoid page layouts that leave most content stacked vertically.
- For flowcharts, place the main success path horizontally from left to right. Put error or rejection branches above or below the relevant decision node, then terminate or return without stretching the main path downward.
- For process rectangles with one line of Chinese text, prefer about 120-170 px width and 36-46 px height. For two lines, prefer about 150-210 px width and 50-62 px height.
- For decision diamonds with short labels, prefer about 96-130 px width and 64-82 px height. Do not use oversized diamonds unless the condition label requires it.
- For start/end terminators with short labels, prefer about 80-110 px width and 34-42 px height.
- Keep text visually centered with minimal empty space. If a label is long, widen the shape before increasing height.
- Keep connector gaps moderate: usually 35-70 px between neighboring shapes on the main path and 30-55 px between a decision and its branch result.
- Do not use a single `swimlane` cell with multiline `value` for ER entities or UML classes. Some draw.io versions render all text in the title band. Build table-like nodes from explicit rectangles, divider lines, and separate text cells, or use a proven table shape that preserves compartments.
- Route connectors so they never pass through unrelated nodes. Add orthogonal waypoints around boxes when a direct line would cross another entity/class/state.
- Keep edge labels close to their edge but offset from nodes and other labels. Avoid labels floating far away from the connector they describe.
- Avoid overlapping opposite-direction edges. For return/failure transitions in state diagrams, route the return path above or below the main path with clear waypoints.

## Use case diagram rules

- Use UML use case diagrams only to show external user goals and system scope. Do not include internal steps, method calls, database operations, or UI event sequences.
- Put actors outside the system boundary and use cases inside the system boundary.
- Render actors with the UML stick-figure actor shape (`shape=umlActor`) by default. Do not use rectangular `<<actor>>` boxes unless the user explicitly asks for a text-only fallback or the target renderer cannot display UML actor shapes.
- Use case names should be concise user goals, usually verb-object phrases such as `登录系统` or `重置密码`.
- Actor-to-use-case associations are plain solid lines without arrowheads.
- `<<include>>` and `<<extend>>` are dashed open-arrow dependencies with correct direction: base use case points to included use case; extension use case points to extended use case.
- Keep the system boundary centered and wide. Put primary actors on the left and external systems or secondary actors on the right.
- Avoid crossing association lines. Reposition use cases or route lines before accepting crossings.

## Sequence diagram rules

- Draw lifelines as explicit vertical dashed edges with visible start and end points, not as 1 px wide `line` vertices. This avoids missing vertical lines when draw.io renders or exports the file.
- Put a participant/object header above every lifeline. The lifeline must start exactly below the header and continue below the last message.
- Use activation bars only for participants that are executing behavior. Activation bars are narrow white rectangles, usually 10-14 px wide, centered on the lifeline.
- Do not draw an activation bar under a passive human actor unless the user explicitly wants user activity duration. For a human actor, the dashed lifeline is usually sufficient.
- Avoid self-loop arrows for simple internal checks such as input-format validation. Prefer a short note attached to the service activation, or omit the internal step and show the next meaningful interaction.
- If a self-call is necessary, draw it as a clear rectangular turnback beside the activation bar with enough width and vertical separation; never use a tiny ambiguous loop.
- Use `alt` fragments for success/failure branches. Keep branch guards visible, and place return messages inside the correct branch area.
- Use solid arrows for calls and dashed open arrows for returns.

## Activity diagram rules

- Use UML activity diagrams to show behavior flow, branching, merging, parallelism, and completion conditions. Do not use activity diagrams to show lifelines, object structure, database schema, or deployment topology.
- Start with a UML initial node and end with a UML activity final node unless the user explicitly requests a partial fragment.
- Render actions as compact rounded rectangles, decision/merge nodes as diamonds, and fork/join nodes as thick black bars when parallel behavior is present.
- Label every outgoing decision edge with a guard such as `[是]`, `[否]`, or a concise condition. Keep guard labels close to their edge.
- Prefer a wide left-to-right main path. Put rejection or exception actions above or below the related decision and route connectors around unrelated nodes.
- Use swimlanes/partitions only when responsibility ownership matters; otherwise avoid them to keep the figure compact.

## ER and class diagram table rules

- Use explicit compartments: title, attributes/fields, and operations where applicable. Do not rely on dashed separator text such as `----` inside one cell.
- Entity/class title text belongs only in the title compartment. Fields and methods must be separate text cells or compartments below the title.
- Keep fields and operations left-aligned with compact padding.
- Preserve line breaks as actual draw.io line breaks. In raw `.drawio` XML, multiline values should contain `&#xa;` or literal newlines after serialization, never `&amp;#xa;`.
- Use orthogonal connectors with entry/exit points chosen to avoid crossing any entity/class box.

## State diagram edge rules

- Keep state boxes compact. For short Chinese labels, use about 90-110 px width and 44-56 px height.
- Main success path should be a clean left-to-right line.
- Failure or rollback transitions must be routed on a separate vertical level from the main path, with waypoints and labels placed on that separate path.
- If two transitions connect the same pair of states in opposite directions, they must not overlap.

## Standards discipline

- Flowcharts: follow ISO 5807 for control/data flowcharts. Use BPMN 2.0.2 only when modeling business processes with pools, lanes, events, activities, gateways, sequence flows, and message flows.
- Use case, activity, sequence, class, state, deployment: follow UML 2.5.1 notation. Use correct arrowheads and line styles.
- ER: use Chen notation for conceptual modeling or Crow's Foot/table notation for database design. Always show keys and cardinalities in logical/physical ER diagrams.
- Architecture: use C4 levels consistently. Do not mix context, container, component, and deployment detail in one figure.

## draw.io implementation notes

Start from this common style unless the diagram-specific notation requires another shape. Use compact spacing values to reduce internal whitespace:

```text
whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor=#000000;fontColor=#000000;fontFamily=SimSun;fontSize=12;strokeWidth=1;spacing=4;spacingTop=3;spacingRight=4;spacingBottom=3;spacingLeft=4;
```

Use this base connector style:

```text
html=1;rounded=0;orthogonalLoop=1;jettySize=auto;strokeColor=#000000;fontColor=#000000;fontFamily=SimSun;fontSize=12;strokeWidth=1;
```

For repeated simple diagrams, use `scripts/create_academic_drawio.py` as a starting point. It supports all standard diagram types:

```bash
python3 workspace/skills/draw-skill/scripts/create_academic_drawio.py flowchart workspace/reports/diagrams/示例流程图.drawio
python3 workspace/skills/draw-skill/scripts/create_academic_drawio.py usecase workspace/reports/diagrams/示例用例图.drawio
python3 workspace/skills/draw-skill/scripts/create_academic_drawio.py activity workspace/reports/diagrams/示例活动图.drawio
python3 workspace/skills/draw-skill/scripts/create_academic_drawio.py sequence workspace/reports/diagrams/示例时序图.drawio
python3 workspace/skills/draw-skill/scripts/create_academic_drawio.py er workspace/reports/diagrams/示例ER图.drawio
python3 workspace/skills/draw-skill/scripts/create_academic_drawio.py class workspace/reports/diagrams/示例类图.drawio
python3 workspace/skills/draw-skill/scripts/create_academic_drawio.py state workspace/reports/diagrams/示例状态图.drawio
python3 workspace/skills/draw-skill/scripts/create_academic_drawio.py architecture workspace/reports/diagrams/示例架构图.drawio
python3 workspace/skills/draw-skill/scripts/create_academic_drawio.py deployment workspace/reports/diagrams/示例部署图.drawio
```

The script is a template helper, not a substitute for reading the relevant standard and adapting the content to the user's system.

## Exporting .drawio files

When the user asks to export, convert, render, or save a `.drawio` file as an image/document, use `scripts/export_drawio.py`. It resolves input files under the shared workspace and writes exports to `workspace/reports/diagrams/`. Prefer `workspace/...` paths in commands.

Supported export formats include `png`, `jpg`, `svg`, and `pdf`. Basic commands:

```bash
python3 workspace/skills/draw-skill/scripts/export_drawio.py workspace/reports/diagrams/网关架构图.drawio --format png
python3 workspace/skills/draw-skill/scripts/export_drawio.py workspace/reports/diagrams/网关架构图.drawio --format jpg
python3 workspace/skills/draw-skill/scripts/export_drawio.py workspace/reports/diagrams/网关架构图.drawio --format svg
python3 workspace/skills/draw-skill/scripts/export_drawio.py workspace/reports/diagrams/网关架构图.drawio --format pdf
```

For image exports, draw.io exports the first page by default. Use `-p <pageIndex>` for a specific page, where page indexes are 1-based:

```bash
python3 workspace/skills/draw-skill/scripts/export_drawio.py workspace/reports/diagrams/网关架构图.drawio --format png --page 1
```

Useful options:

- `-t`: transparent background for PNG.
- `-b <border>`: add a border around the exported diagram, for example `-b 20`.
- `-s <scale>`: scale the output, for example `-s 2` for higher-resolution PNG.
- `--width <width>` or `--height <height>`: fit output to a target dimension while preserving aspect ratio.
- `-a`: export all pages for PDF.
- `-r`: recursively export `.drawio` files when the input is a folder.

Batch export examples:

```bash
python3 workspace/skills/draw-skill/scripts/export_drawio.py workspace/reports/diagrams/网关架构图.drawio --format png --output workspace/reports/diagrams/网关架构图.png
```

After exporting, confirm the output file exists and is non-empty, for example:

```bash
test -s /path/to/output.png
```

If the script reports that PNG/PDF export is unavailable, return the generated `.drawio` or `.svg` file path immediately instead of attempting package installation.

## Validation checklist

Before final response:

1. Run XML parsing, for example `python3 -m xml.etree.ElementTree file.drawio`.
2. Search the output for forbidden colorful fills such as `#dae8fc`, `#d5e8d4`, `#f8cecc`, gradients, or shadows.
3. Confirm `fontFamily=SimSun` appears on all text-bearing nodes and labels.
4. Confirm line widths are reasonable and there are no disconnected important edges.
5. For sequence diagrams, confirm every participant has a visible dashed lifeline and ambiguous self-loop messages are not used for simple internal checks.
6. For use case diagrams, confirm actors use `shape=umlActor` and are not rectangular `<<actor>>` boxes.
7. For activity diagrams, confirm there is a UML initial node and activity final node, decision exits have guard labels, and fork/join bars are used only for real parallel behavior.
8. For ER/class diagrams, confirm fields and methods are not rendered inside the title compartment and connectors do not cross unrelated boxes.
9. For ER/class diagrams, search the file for `&amp;#xa;`; if present, fix it because draw.io will display those line breaks as text instead of new lines.
10. For state diagrams, confirm reverse/failure edges do not overlap the main path and labels sit near their lines.
11. Confirm the figure follows the selected specification rather than a free-form sketch.
12. When exporting, confirm the exported file exists and is non-empty, and tell the user the output path and format.
13. Tell the user which standard file was followed and where the `.drawio` output was written.
14. If an export was requested, ensure the exported file path is under `workspace/reports/diagrams/` and mention that this path can be sent as a Feishu file attachment.
