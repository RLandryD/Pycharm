import com.sap.gateway.ip.core.customdev.util.Message;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.StringWriter;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.Charset;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Map;
import javax.xml.transform.dom.DOMSource;
import javax.xml.transform.stream.StreamResult;
import javax.xml.transform.Transformer;
import javax.xml.transform.TransformerException;
import javax.xml.transform.TransformerFactory;
import javax.xml.xpath.XPath;
import javax.xml.xpath.XPathConstants;
import javax.xml.xpath.XPathExpressionException;
import javax.xml.xpath.XPathFactory;
import javax.xml.parsers.DocumentBuilder;
import javax.xml.parsers.DocumentBuilderFactory;
import javax.xml.parsers.ParserConfigurationException;
import org.xml.sax.SAXException;
import org.w3c.dom.Document;
import org.w3c.dom.Node;
import org.w3c.dom.NodeList;
import org.w3c.dom.NamedNodeMap;
import org.w3c.dom.Attr;

class XMLHelper {

	private XPath xPath;
	
	public XMLHelper() {
		XPathFactory factory = XPathFactory.newInstance();
		xPath = factory.newXPath();
	}
	
	// Create a new node with a tag name and value
	public Node createNode(String name, String value, Node newParentNode, Document doc) {		
		Node newNode = doc.createElement(name);
		newNode.setTextContent(value);		
		newParentNode.appendChild(newNode);
		return newNode;
	}

	// Create a new node based on an existing node
	public Node createNode(String name, Node baseNode, Node newParentNode, Document doc) {
		if (baseNode != null) {
			return createNode(name, baseNode.getNodeName(), newParentNode, doc);
		} else {
			return null;
		}
	}

	// Retrieve a node of a given path for a specific parent node
	public Node retrieveNodeOfParentNode(String path, Node parentNode) throws XPathExpressionException {		
		return (Node) this.xPath.evaluate(path, parentNode, XPathConstants.NODE);		
	}

	// Retrieve a node of a given path for a specific parent node
	public Node retrieveNodeOfDocument(String path, Document doc) throws XPathExpressionException {
		return (Node) this.xPath.evaluate(path, doc, XPathConstants.NODE);
	}
	
	public Document createXMLNodes(String sourceString) {
		DocumentBuilderFactory builderFactory = DocumentBuilderFactory.newInstance();
		DocumentBuilder builder = null;
	    builder = builderFactory.newDocumentBuilder();
	    InputStream is = new ByteArrayInputStream(sourceString.getBytes(StandardCharsets.UTF_8));
		return builder.parse(is);
		return null;
	}
}

def Message processData(Message message) {
    
    def messageLog = messageLogFactory.getMessageLog(message);
	
	//get payload (compound employee api query result)
	Document body = message.getBody(org.w3c.dom.Document);
	String rawbody = message.getBody(java.lang.String) as String;
    
	//initiate xml helper
	XMLHelper xmlHelper = new XMLHelper();
	
	//get process properties  
	String extensibilityUsage         = message.getProperty("EXTENSIBILITY_USAGE");
	String fullTransmissionStartDate  = message.getProperty("FULL_TRANSMISSION_START_DATE");	
	String replicationTargetSystem    = message.getProperty("REPLICATION_TARGET_SYSTEM");
	
    //create message ids for erp inbound message
	String messIDEx = java.util.UUID.randomUUID().toString();
	String messID = messIDEx.replaceAll("-", "").toUpperCase();
	message.setHeader("SapMessageId", messID);
	message.setHeader("SapMessageIdEx", messIDEx);
	
	//define erp target interface structure (including namespace)
	String targetXML = "<ns5:EmployeeMasterDataBundleReplicationRequest xmlns:ns5=\"http://sap.com/xi/PASEIN\"><MessageHeader><ID/><UUID/><CreationDateTime/><RecipientBusinessSystemID/></MessageHeader><EmplMasterDataBndlReplReq><FullTransmissionStartDate/></EmplMasterDataBndlReplReq></ns5:EmployeeMasterDataBundleReplicationRequest>";
	
	//get list of queried employee master data records
	Node queryCompoundEmployeeResponseNode = xmlHelper.retrieveNodeOfDocument("//queryCompoundEmployeeResponse", body);
	
	//create erp inbound message
	Document employeeMasterDataBundleReplicationRequest;
	try {
	    employeeMasterDataBundleReplicationRequest = xmlHelper.createXMLNodes(targetXML);
	} catch (Exception e) {
	    if(messageLog != null && e != null){
            messageLog.setStringProperty("Exception occured while trying to create ERP inbound message Document ", e.getClass().getName() + " - " + e.getMessage());
            return;
        }
	}
	
	// define message header node
	Node messageHeader = xmlHelper.retrieveNodeOfDocument("//MessageHeader", employeeMasterDataBundleReplicationRequest);
	
	//set header ids
	Node id = xmlHelper.retrieveNodeOfParentNode("./ID", messageHeader);
	id.setTextContent(messID);
	Node uuid = xmlHelper.retrieveNodeOfParentNode("./UUID", messageHeader);
	uuid.setTextContent(messIDEx);
	
	//set replication target system
	Node recipientBusinessSystemID = xmlHelper.retrieveNodeOfParentNode("./RecipientBusinessSystemID", messageHeader);
	recipientBusinessSystemID.setTextContent(replicationTargetSystem);
	
	//set creation time stamp
	Node creationDateTime = xmlHelper.retrieveNodeOfParentNode("./CreationDateTime", messageHeader);
	Date date = new Date();
	SimpleDateFormat formater = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'");
	creationDateTime.setTextContent(formater.format(date));
	
	//define replication request node
	Node emplMasterDataBndlReplReq = xmlHelper.retrieveNodeOfDocument("//EmplMasterDataBndlReplReq", employeeMasterDataBundleReplicationRequest);
	
	//set full transmission start date
	Node fullTransmissionStartDateNode = xmlHelper.retrieveNodeOfParentNode("./FullTransmissionStartDate", emplMasterDataBndlReplReq);
	fullTransmissionStartDateNode.setTextContent(fullTransmissionStartDate);
		
	//create node source system output 
	Node sourceSystemOutputNode = xmlHelper.createNode("SourceSystemOutput", new String(), emplMasterDataBndlReplReq, employeeMasterDataBundleReplicationRequest);
    sourceSystemOutputNode.setTextContent(rawbody);
	
	//set attributes of node source system output
    NamedNodeMap attributes = sourceSystemOutputNode.getAttributes();
    Attr attributeId = employeeMasterDataBundleReplicationRequest.createAttribute("id");
    attributeId.setValue(messID);
    Attr attributeFileName = employeeMasterDataBundleReplicationRequest.createAttribute("fileName");
    attributeFileName.setValue("PayloadSFAPI");
    attributes.setNamedItem(attributeId);  
    attributes.setNamedItem(attributeFileName);  
		
	//set mapped target message as payload
	message.setBody(employeeMasterDataBundleReplicationRequest);
	
	return message;
}
