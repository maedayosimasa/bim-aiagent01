def find_rooms(graph):

    rooms=[]


    for node,data in graph.nodes(data=True):

        if data.get("type")=="Room":

            rooms.append(node)


    return rooms