from src.backend.graph.builder import build_graph

graph = build_graph()

print("===== Graph Information =====")
print("Nodes :", graph.number_of_nodes())
print("Edges :", graph.number_of_edges())

print("\n===== Node List =====")

for node, data in graph.nodes(data=True):
    print(node, data)

print("\n===== Edge List =====")

for edge in graph.edges():
    print(edge)