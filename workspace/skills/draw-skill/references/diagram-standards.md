# Academic software engineering diagram standards

Use this reference after reading `SKILL.md` when creating or revising a diagram. The local Chinese standard documents in `references/绘图规范` are the authoritative working notes for this skill.

## Source hierarchy

1. Flowcharts: ISO 5807:1985 for information-processing flowcharts; OMG BPMN 2.0.2 when modeling business processes with participants, events, gateways, pools, lanes, sequence flows, and message flows.
2. Use case, activity, sequence, class, state, and deployment diagrams: OMG UML 2.5.1.
3. ER diagrams: Peter Chen's entity-relationship model and, for implementation-facing database diagrams, Crow's Foot conventions.
4. Architecture diagrams: C4 Model official guidance for system context, container, component, dynamic, and deployment views; UML Component Diagram when formal component/interface notation is required.

## Publication profile

- Use black, white, and gray only. Prefer white fills and black strokes.
- Use SimSun/宋体 in every text-bearing cell: `fontFamily=SimSun`.
- Use `fontSize=12` for body text, approximating Chinese 小四 in draw.io. Use 14 only for an optional diagram title and 10 for secondary notes.
- Use `strokeWidth=1` by default. Use 1.2 only for outer boundaries.
- Avoid decorative styling, shadows, gradients, rounded card-like layouts, and color-coded semantics.
- Keep labels concise enough to remain legible after paper-column scaling.
- Use orthogonal connectors unless the diagram type convention requires otherwise.
- Prefer wide, shallow layouts for academic papers. Use A4 landscape by default and arrange the main reading path left-to-right where the notation allows.
- Fit node dimensions closely to text. Avoid rectangles or diamonds with large internal whitespace; widen long labels before increasing node height.

## Compact layout targets

- Main-path flowchart/process node: one-line label `120-170 x 36-46 px`; two-line label `150-210 x 50-62 px`.
- Flowchart decision node: short condition `96-130 x 64-82 px`.
- Start/end terminator: short label `80-110 x 34-42 px`.
- Horizontal gap on main path: usually `35-70 px`.
- Branch gap from a decision to an error/result node: usually `30-55 px`.
- Simple diagram target aspect ratio: content width should usually be at least `2.5x` content height.

## Sequence diagram rendering rules

- Represent lifelines with dashed edges or connectors that have real vertical extent. Avoid 1 px wide line vertices because draw.io/exporters may hide them.
- Headers should be compact white rectangles, 100-140 px wide.
- Activation bars should be white rectangles with black borders, 10-14 px wide, centered on the participant's lifeline.
- Do not create self-loop messages for routine internal validation unless the self-call is essential to the model. Use a note or skip the internal detail.
- Put success/failure alternatives inside an `alt` fragment with clear guards such as `[认证通过]` and `[认证失败]`.

## Use case diagram rendering rules

- Actors must be outside the system boundary; use cases must be inside it.
- Use ovals for use cases and UML stick-figure actors (`shape=umlActor`) for actors. Rectangular `<<actor>>` boxes are only a fallback when explicitly requested.
- Actor associations are solid lines without arrowheads.
- `<<include>>` and `<<extend>>` are dashed open-arrow dependencies with labels near the line.
- Avoid crossing lines; place primary actors left and secondary external systems right.

## Activity diagram rendering rules

- Use a UML initial node (`shape=startState`) and activity final node (`shape=endState`) for complete activity diagrams.
- Actions are compact rounded rectangles. Decision and merge nodes are diamonds. Fork and join nodes are thick black bars only when modeling parallel behavior.
- Every outgoing edge from a decision node should have a visible guard label close to the line.
- Keep the main path left-to-right for paper figures. Place rejection or exception actions above or below the decision that creates them.
- Route connectors orthogonally around unrelated nodes; never let branch lines run through action boxes or labels.

## ER and class diagram rendering rules

- Do not create ER entities or UML classes as one `swimlane` cell with all text in the `value`. In draw.io this can render fields/methods inside the title band.
- Use explicit compartments: outer rectangle, title text, horizontal divider, and separate left-aligned text cells for fields and methods.
- Multiline field/method text must not be double-escaped. Raw `.drawio` should not contain `&amp;#xa;`; use `&#xa;` or let the XML writer serialize literal newlines.
- Keep connector routes orthogonal and outside unrelated boxes. If a direct connector would cross an entity/class, add waypoints.
- Place relationship labels close to the corresponding connector segment and away from entity/class interiors.

## State diagram rendering rules

- State boxes should be compact and close to the text.
- Main transitions should occupy one clear horizontal path.
- Failure, rollback, timeout, and logout transitions should use separate routed paths so edge lines and labels do not overlap.
- Never place a label far from its transition line.

## Diagram-specific routing

- `flowchart`: Read `references/绘图规范/01-流程图绘制规范.md`.
- `usecase`: Read `references/绘图规范/08-用例图绘制规范.md`.
- `activity`: Read `references/绘图规范/09-活动图绘制规范.md`.
- `sequence`: Read `references/绘图规范/02-时序图绘制规范.md`.
- `er`: Read `references/绘图规范/03-ER图绘制规范.md`.
- `class`: Read `references/绘图规范/04-类图绘制规范.md`.
- `state`: Read `references/绘图规范/05-状态图绘制规范.md`.
- `architecture`: Read `references/绘图规范/06-架构图绘制规范.md`.
- `deployment`: Read `references/绘图规范/07-部署图绘制规范.md`.

Always read `references/绘图规范/00-总则-论文软件工程图绘制规范.md` before producing final output.

## Required output checks

1. XML parses successfully.
2. File extension is `.drawio`.
3. Every `mxCell` with text uses `fontFamily=SimSun` and black text.
4. No colored semantic fills are used. Acceptable fill values: `#ffffff`, `none`, or omitted.
5. Edges are connected by `source` and `target` whenever possible.
6. The content bounding box is wide and shallow unless the user explicitly requests a vertical figure or the notation requires one.
7. Node sizes are close to text dimensions and do not contain excessive blank space.
8. In sequence diagrams, lifelines are visible dashed vertical connectors and internal checks are not rendered as unclear self-loops.
9. In use case diagrams, actors are outside the system boundary, use cases are inside, actors use `shape=umlActor`, and normal actor associations have no arrowheads.
10. In activity diagrams, initial/final nodes use UML notation, decision exits have guard labels, and fork/join bars appear only for actual parallel behavior.
11. In ER/class diagrams, fields and methods are in explicit compartments and not in the title band.
12. In ER/class diagrams, raw XML does not contain `&amp;#xa;` in multiline field/method values.
13. Connectors do not pass through unrelated nodes.
14. In state diagrams, reverse/failure transitions do not overlap the main path.
15. Diagram respects the chosen notation: no mixed ER/class/flow/deployment semantics unless explicitly requested as a hybrid explanatory figure.
