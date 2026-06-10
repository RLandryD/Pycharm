"""schema_fixtures.py — reusable XSD fixtures + a dependency-free structural
conformance check, so the schema resolver + sample-payload generator can be
tested end-to-end in the sandbox (the corpus has no real xsd here).

Each fixture is a representative CPI message shape. `sample_conforms` verifies a
generated sample is well-formed AND instantiates every element the schema
declares (our generator emits all declared elements, including choice branches).
"""
import xml.etree.ElementTree as ET

# name -> (xsd, root_element_name). Covers the shapes that actually show up in
# CPI: typed leaves, nesting, attributes, repeating/unbounded, named-type refs.
FIXTURES = {
    "simple_typed": (
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
        '<xs:element name="Ping"><xs:complexType><xs:sequence>'
        '<xs:element name="Id" type="xs:int"/>'
        '<xs:element name="When" type="xs:dateTime"/>'
        '<xs:element name="Ok" type="xs:boolean"/>'
        '</xs:sequence></xs:complexType></xs:element></xs:schema>', "Ping"),
    "nested": (
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
        '<xs:element name="Order"><xs:complexType><xs:sequence>'
        '<xs:element name="Header"><xs:complexType><xs:sequence>'
        '<xs:element name="Number" type="xs:string"/></xs:sequence>'
        '</xs:complexType></xs:element>'
        '<xs:element name="Total" type="xs:decimal"/>'
        '</xs:sequence></xs:complexType></xs:element></xs:schema>', "Order"),
    "attributes": (
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
        '<xs:element name="Item"><xs:complexType><xs:sequence>'
        '<xs:element name="Name" type="xs:string"/></xs:sequence>'
        '<xs:attribute name="sku" type="xs:string"/>'
        '<xs:attribute name="qty" type="xs:int"/>'
        '</xs:complexType></xs:element></xs:schema>', "Item"),
    "repeating": (
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
        '<xs:element name="Lines"><xs:complexType><xs:sequence>'
        '<xs:element name="Line" maxOccurs="unbounded"><xs:complexType>'
        '<xs:sequence><xs:element name="Sku" type="xs:string"/></xs:sequence>'
        '</xs:complexType></xs:element></xs:sequence></xs:complexType>'
        '</xs:element></xs:schema>', "Lines"),
    "named_type_ref": (
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
        '<xs:element name="Doc" type="DocType"/>'
        '<xs:complexType name="DocType"><xs:sequence>'
        '<xs:element name="Title" type="xs:string"/>'
        '<xs:element name="Pages" type="xs:int"/>'
        '</xs:sequence></xs:complexType></xs:schema>', "Doc"),
}


def _local(tag):
    return tag.split("}")[-1] if "}" in tag else tag


def declared_elements(xsd):
    """Every element NAME the schema declares."""
    root = ET.fromstring(xsd)
    return {e.get("name") for e in root.iter()
            if _local(e.tag) == "element" and e.get("name")}


def sample_conforms(sample, xsd):
    """Well-formed AND every declared element instantiated at least once."""
    try:
        tree = ET.fromstring(sample)
    except Exception:
        return False
    present = {_local(el.tag) for el in tree.iter()} | {_local(tree.tag)}
    return declared_elements(xsd) <= present
