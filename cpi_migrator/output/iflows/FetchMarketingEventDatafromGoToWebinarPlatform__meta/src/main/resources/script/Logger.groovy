import com.sap.gateway.ip.core.customdev.util.Message;
import java.util.HashMap;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Callable;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import groovy.transform.Field;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.OpenOption;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.util.Map;

import javax.xml.transform.TransformerFactory;
import javax.xml.transform.Transformer;
import javax.xml.transform.TransformerException;
import org.w3c.dom.Node;




import javax.xml.transform.dom.DOMSource;
import javax.xml.transform.stream.StreamResult;
import javax.xml.transform.TransformerFactory;
import javax.xml.transform.Transformer;

import org.w3c.dom.Node;




// Ilya Stepanov
// SAP AG
// 24.06.2016
// this script is not part of standard HCI

// to do
// remove METVIEWER_LAST_TIMESTAMP headers 
// 6. setting SAP-properties/headers (ticket). change value of PROPERTY_LAST_TIMESTAMP to SAP_METVIEWER_LAST_TIMESTAMP
// 7. todo - read iflow name from MANIFEST
// 8. LOG* yes. no logic
// 9. EXCLUDE logic
// 10. thread issues - test in http adapter, in exception case with.without threading


@Field String IFLOW_NAME = 'fieldglass'

@Field String FILE_LOGGING_MODE = 'ALWAYS';  //ALWAYS, NEVER , PROPERTY
@Field String MPL_LOGGING_MODE = 'ALWAYS';   //ALWAYS, NEVER, PROPERTY

@Field List EXCLUDE_PROPERTIES = ['SAP_MessageProcessingLogID', 'SAP_MonitoringStateProperties', 'MplMarkers', 'SAP_MessageProcessingLog'];
@Field List EXCLUDE_HEADERS = [''];

@Field String LOG_HEADERS          = 'YES';   //YES / NO
@Field String LOG_PROPERTIES       = 'YES';   //YES / NO
@Field String LOG_BODY_INFO        = 'YES';   //YES / NO
@Field String LOG_EXCEPTION        = 'YES';   //YES / NO
@Field String LOG_OTHER            = 'YES';   //YES / NO
@Field String LOG_BODY             = 'YES';   //YES / NO
@Field String LOG_ATTACHMENTS_INFO = 'YES';   //YES / NO
@Field String LOG_ATTACHMENTS_BODY = 'YES';   //YES / NO
@Field String LOG_SOAP_HEADERS_INFO = 'YES';   //YES / NO
@Field String MODIFY_EXCHANGE      = 'YES';   //YES / NO

@Field String PROPERTY_INVOCATION_COUNTER 		= 'SAP_METVIEWER_INVOCATION_COUNTER'
@Field String PROPERTY_LAST_TIMESTAMP 			= 'METVIEWER_LAST_TIMESTAMP'
@Field String PROPERTY_ENABLE_MPL_LOGGING 		= 'ENABLE_LOGGING'
@Field String PROPERTY_ENABLE_FILE_LOGGING 		= 'ENABLE_FILE_LOGGING'

@Field Logger log = LoggerFactory.getLogger("com.sap.hci.metviewer."+IFLOW_NAME);
    

// if you use this method, you need to copy the script for every usage and adapt the filename (000)
def Message processData(Message message) {
//    Logger log = LoggerFactory.getLogger(this.getClass());
    
    try {
        
        //def props = message.getProperties();
        //def some_value = props.get('some_property');
        
        processHeadersAndProperties(    IFLOW_NAME+"_000",           message);
        processBody(                    IFLOW_NAME+"_000_payload",   message);
        
    } catch (Exception ex) {
        log.error("processData error",ex);
    }
    return message;
}


// you can use _010, _020 etc methods to reuse same script from multiple places in the iflow
def Message log_010(Message message) {
	def props = message.getHeaders();
	def String log_id = props.get("SAP_ApplicationID");
	    
    try {
        increaseInternalHeaders(message);
        increaseInternalProperties(message);
        processHeadersAndProperties(    log_id+'_REQ_{COUNTER}_info',           message);
        processBody(                    log_id+'_REQ_{COUNTER}_payload',   message);
        processAttachmentBody(          log_id+'_REQ_{COUNTER}_',   message);

    } catch (Exception ex) {
        log.error("processData error",ex);
    }
    return message;
}

def Message log_020(Message message) {
	def props = message.getHeaders();
	def String log_id = props.get("SAP_ApplicationID");
    
    try {
        increaseInternalHeaders(message);
        increaseInternalProperties(message);
        processHeadersAndProperties(    log_id+"_RSP_{COUNTER}_info",           message);
        processBody(                    log_id+"_RSP_{COUNTER}_payload",   message);
        processAttachmentBody(          log_id+'_RSP_{COUNTER}_',   message);
    } catch (Exception ex) {
        log.error("processData error",ex);
    }
    return message;
}


def Message log_030(Message message) {
    
    try {
        increaseInternalHeaders(message);
        increaseInternalProperties(message);
        processHeadersAndProperties(    IFLOW_NAME+"_030_{COUNTER}_info",           message);
        processBody(                    IFLOW_NAME+"_030_{COUNTER}_payload",   message);
        processAttachmentBody(          IFLOW_NAME+'_010_{COUNTER}_',   message);
    } catch (Exception ex) {
        log.error("processData error",ex);
    }
    return message;
}


def Message log_040(Message message) {
    
    try {
        increaseInternalHeaders(message);
        increaseInternalProperties(message);
        processHeadersAndProperties(    IFLOW_NAME+"_040_{COUNTER}_info",           message);
        processBody(                    IFLOW_NAME+"_040_{COUNTER}_payload",   message);
        processAttachmentBody(          IFLOW_NAME+'_010_{COUNTER}_',   message);
    } catch (Exception ex) {
        log.error("processData error",ex);
    }
    return message;
}

def Message log_050(Message message) {
    
    try {
        increaseInternalHeaders(message);
        increaseInternalProperties(message);
        processHeadersAndProperties(    IFLOW_NAME+"_050_{COUNTER}_info",           message);
        processBody(                    IFLOW_NAME+"_050_{COUNTER}_payload",   message);
        processAttachmentBody(          IFLOW_NAME+'_010_{COUNTER}_',   message);
    } catch (Exception ex) {
        log.error("processData error",ex);
    }
    return message;
}

def Message log_060(Message message) {
    
    try {
        increaseInternalHeaders(message);
        increaseInternalProperties(message);
        processHeadersAndProperties(    IFLOW_NAME+"_060_{COUNTER}_info",           message);
        processBody(                    IFLOW_NAME+"_060_{COUNTER}_payload",   message);
        processAttachmentBody(          IFLOW_NAME+'_010_{COUNTER}_',   message);
    } catch (Exception ex) {
        log.error("processData error",ex);
    }
    return message;
}

def Message log_070(Message message) {
    
    try {
        increaseInternalHeaders(message);
        increaseInternalProperties(message);
        processHeadersAndProperties(    IFLOW_NAME+"_070_{COUNTER}_info",           message);
        processBody(                    IFLOW_NAME+"_070_{COUNTER}_payload",   message);
        processAttachmentBody(          IFLOW_NAME+'_010_{COUNTER}_',   message);
    } catch (Exception ex) {
        log.error("processData error",ex);
    }
    return message;
}

def Message error(Message message) {
    
    try {
        processHeadersAndProperties(    IFLOW_NAME+"_error_info",      message);
        processBody(                    IFLOW_NAME+"_error_payload",   message);
    } catch (Exception ex) {
        log.error("processData error",ex);
    }
    return message;
}



def boolean isTrue(String str) {
    if ('ALWAYS'.equalsIgnoreCase(str)) {
        return true;
    } else if ('TRUE'.equalsIgnoreCase(str)) {
       return true;
    } else if ('YES'.equalsIgnoreCase(str)) {
       return true;
    } else if ('ON'.equalsIgnoreCase(str)) {
       return true;
    }
    return false;
}    




def void increaseInternalHeaders(Message message) {

   def props = message.getHeaders();
   def StringBuffer counter = props.get(PROPERTY_INVOCATION_COUNTER);
   
   if (counter==null) {
        counter = new StringBuffer();
        counter.append("0");
        if (isTrue(MODIFY_EXCHANGE)) {
           message.setHeader(PROPERTY_INVOCATION_COUNTER, counter);
        }
    }
    
    int cnt = Integer.valueOf(counter.toString());
    cnt = cnt+1;
    def counterS = ""+cnt;
    counter.setLength(0);
    counter.append(counterS);
}

def void increaseInternalProperties(Message message) {

   def props = message.getProperties();
   def StringBuffer counter = props.get(PROPERTY_INVOCATION_COUNTER);
   
   if (counter==null) {
        counter = new StringBuffer();
        counter.append("0");
        
        if (isTrue(MODIFY_EXCHANGE)) {
           message.setProperty(PROPERTY_INVOCATION_COUNTER, counter);
        }
    }
    
    int cnt = Integer.valueOf(counter.toString());
    cnt = cnt+1;
    def counterS = ""+cnt;
    counter.setLength(0);
    counter.append(counterS);
    
}

def Message processData(String prefix, Message message) {
    try {
        processBody(prefix+"_payload", message);
        processHeadersAndProperties(prefix, message);
    } catch (Exception ex00) {
        log.error("processData error",ex00)
        StringWriter sw = new StringWriter();
        ex00.printStackTrace(new PrintWriter(sw));
        log.error(sw.toString());
    }
    return message;
}


def Map excludeEntries(Map map, List excluded) {
    def newMap = new HashMap();
    newMap.putAll(map);
    newMap.keySet().removeAll(excluded);
    return newMap;
    
}



def void processBody(String prefix_with_vars, Message message) {
    def byte[] body_bytes = null;
    try {
        
        def prefix = resolveVariables(prefix_with_vars, message);
        
        def enable = false;
    
        if ('YES'.equalsIgnoreCase(LOG_BODY)) {
            enable = true;
        }
            
        if (!enable) return;
        
        if (message==null) {
            body_bytes = new byte[0];
        } else if (message.getBody() == null) {
            body_bytes = new byte[0];
        } else {
            body_bytes = message.getBody(byte[].class);
        }
        
        def props = message.getProperties();
        def property_ENABLE_MPL_LOGGING = props.get(PROPERTY_ENABLE_MPL_LOGGING);
        def property_ENABLE_FILE_LOGGING = props.get(PROPERTY_ENABLE_FILE_LOGGING);
        
        def mpl_enabled = false;
        if (isTrue(MPL_LOGGING_MODE)) {
            mpl_enabled = true;
        } else if ("PROPERTY".equalsIgnoreCase(MPL_LOGGING_MODE)) {
            if (isTrue(property_ENABLE_MPL_LOGGING)) {
                mpl_enabled = true;
            }
        }
        
        
        def file_enabled = false;
        if (isTrue(FILE_LOGGING_MODE)) {
            file_enabled = true;
        } else if ("PROPERTY".equalsIgnoreCase(FILE_LOGGING_MODE)) {
            if (isTrue(property_ENABLE_FILE_LOGGING)) {
                file_enabled = true;
            }
        }
        
        if (mpl_enabled) {
            def messageLog = messageLogFactory.getMessageLog(message);
            def mpl_prefix = prefix;
            if (prefix.startsWith(IFLOW_NAME+'_')) {
               mpl_prefix = prefix.substring(IFLOW_NAME.length()+1);
            }
            messageLog.addAttachmentAsString(mpl_prefix, new String(body_bytes), "text/plain");
        }
        
        if (file_enabled) {
            ExecutorService pool = Executors.newSingleThreadExecutor();
            def task = {c -> pool.submit( c as Callable)}
            task{ saveFile(""+prefix+".xml", body_bytes) }
        }
        
    } catch (Exception ex01) {
        log.error("cannot save body",ex01);
        StringWriter sw = new StringWriter();
        ex01.printStackTrace(new PrintWriter(sw));
        log.info(sw.toString());
    }
}



def void processAttachmentBody(String prefix_with_vars, Message message) {
    def byte[] body_bytes = null;
    try {
        
        def prefix = resolveVariables(prefix_with_vars, message);
        
        def enable = false;
    
        if (isTrue(LOG_ATTACHMENTS_BODY)) {
            enable = true;
        }
            
        if (!enable) return;
        
        def props = message.getProperties();
        def property_ENABLE_MPL_LOGGING = props.get(PROPERTY_ENABLE_MPL_LOGGING);
        def property_ENABLE_FILE_LOGGING = props.get(PROPERTY_ENABLE_FILE_LOGGING);
        
        def mpl_enabled = false;
        if (isTrue(MPL_LOGGING_MODE)) {
            mpl_enabled = true;
        } else if ("PROPERTY".equalsIgnoreCase(MPL_LOGGING_MODE)) {
            if (isTrue(property_ENABLE_MPL_LOGGING)) {
                mpl_enabled = true;
            }
        }
        
        
        def file_enabled = false;
        if (isTrue(FILE_LOGGING_MODE)) {
            file_enabled = true;
        } else if ("PROPERTY".equalsIgnoreCase(FILE_LOGGING_MODE)) {
            if (isTrue(property_ENABLE_FILE_LOGGING)) {
                file_enabled = true;
            }
        }
        
        
        def attachments = message.getAttachments();
       	if ((attachments!=null)&&(!attachments.isEmpty())) {
   	         def base_mpl_prefix = prefix;
             if (prefix.startsWith(IFLOW_NAME)) {
                base_mpl_prefix = prefix.substring(IFLOW_NAME.length()+1);
             }
             
             
   	         attachments.each { key -> 
   	           def attachment_body = readInputStream(attachments.get(key.getKey()).getInputStream());
   	           if (mpl_enabled) {
                 def mpl_prefix = base_mpl_prefix + key.getKey();
                 def messageLog = messageLogFactory.getMessageLog(message);
                 messageLog.addAttachmentAsString(mpl_prefix, new String(attachment_body, "UTF-8"), "text/plain");
               }
        
		       if (file_enabled) {
		         ExecutorService pool = Executors.newSingleThreadExecutor();
		         def task = {c -> pool.submit( c as Callable)}
		         task{saveFile(""+prefix+key.getKey()+".bin", attachment_body)}
		       }
   	        }
   	      }
   	      
    } catch (Exception ex01) {
        log.error("cannot save attachment body",ex01);
        StringWriter sw = new StringWriter();
        ex01.printStackTrace(new PrintWriter(sw));
        log.info(sw.toString());
    }
}

def String resolveVariables(String str, Message message) {

    def counter = message.getHeaders().get(PROPERTY_INVOCATION_COUNTER);
    if (counter == null) {
       counter = '0';
    }
	def result = str.replaceAll(~/\{COUNTER\}/, counter);
	
	return result;

}



  boolean skipNL;
  String printXML(org.w3c.dom.Node rootNode) {
    String tab = "";
    skipNL = false;
    return(printXML(rootNode, tab));
}
  String printXML(org.w3c.dom.Node rootNode, String tab) {
    String print = "";
    if(rootNode.getNodeType()==org.w3c.dom.Node.ELEMENT_NODE) {
        print += "\n"+tab+"<"+rootNode.getNodeName()+">";
    }
    org.w3c.dom.NodeList nl = rootNode.getChildNodes();
    if(nl.getLength()>0) {
        for (int i = 0; i < nl.getLength(); i++) {
            print += printXML(nl.item(i), tab+"  ");    // \t
        }
    } else {
        if(rootNode.getNodeValue()!=null) {
            print = rootNode.getNodeValue();
        }
        skipNL = true;
    }
    if(rootNode.getNodeType()==org.w3c.dom.Node.ELEMENT_NODE) {
        if(!skipNL) {
            print += "\n"+tab;
        }
        skipNL = false;
        print += "</"+rootNode.getNodeName()+">";
    }
    return(print);
}


def void processHeadersAndProperties(String prefix_with_vars, Message message) {
    try {
    
		log.debug("processHeadersAndProperties: " +prefix_with_vars +" START");

	    log.debug("processHeadersAndProperties: " +prefix_with_vars +" MAIN01");
        
        def prefix = resolveVariables(prefix_with_vars, message);
        
        def StringBuffer sb_html = new StringBuffer();
        def StringBuffer sb_text = new StringBuffer();
		
        def map = message.getHeaders();
        
        def enable = false;
        if ('YES'.equalsIgnoreCase(LOG_HEADERS)) {
            enable = true;
        }
        
        if (enable) {
            map = excludeEntries(map, EXCLUDE_HEADERS);
            dumpProperties_TEXT_escaped("Headers", map, sb_html);
            dumpProperties_TEXT("Headers", map, sb_text);
        }
        
        
        map = message.getProperties();
        
        
        log.debug("processHeadersAndProperties: " +prefix_with_vars +"  PROPERTIES01");
        
        enable = false;
        if ('YES'.equalsIgnoreCase(LOG_PROPERTIES)) {
            enable = true;
        }

        if (enable) {
            map = excludeEntries(map, EXCLUDE_PROPERTIES);
            dumpProperties_TEXT_escaped("Properties", map, sb_html);
            dumpProperties_TEXT("Properties", map, sb_text);
        }
        
        
        log.debug("processHeadersAndProperties: " +prefix_with_vars +"  EXCEPTION01");
        
        enable = false;
        if ('YES'.equalsIgnoreCase(LOG_EXCEPTION)) {
            enable = true;
        }
        
        if (enable) {
            
        def ex = map.get("CamelExceptionCaught");
        if (ex!=null) {
            
            def exmap = new HashMap();
            exmap.put("exception",ex);
            exmap.put("getCanonicalName",ex.getClass().getCanonicalName());
            exmap.put("getMessage",ex.getMessage());
            
            StringWriter swe = new StringWriter();
            ex.printStackTrace(new PrintWriter(swe));
            exmap.put("stacktrace",swe.toString());
            
            if (ex.getClass().getCanonicalName().equals("org.apache.camel.component.ahc.AhcOperationFailedException")) {
                exmap.put("responseBody",safeEscapeXml(ex.getResponseBody()));
                exmap.put("responseBody.className",safeClassName(ex));
                exmap.put("getStatusText",ex.getStatusText());
                exmap.put("getStatusCode",ex.getStatusCode());
            }
            
            if (ex instanceof org.apache.cxf.interceptor.Fault) {
                exmap.put("getDetail",safeEscapeXml(ex.getDetail()));
                exmap.put("getDetail.className",safeClassName(ex.getDetail()));
                exmap.put("getFaultCode",ex.getFaultCode());
                exmap.put("getMessage",ex.getMessage());
                exmap.put("getStatusCode",""+ex.getStatusCode());
                exmap.put("hasDetails",""+ex.hasDetails());
                
                //message.getHeaders().put("SoapFaultMessage", ex.getMessage());
                exmap.put("getCause",""+ex.getCause());

				if (ex.getCause()!=null) {
					def cause_message = ex.getCause().getMessage();
					if (ex.getCause() instanceof org.apache.cxf.transport.http.HTTPException) {
						cause_message = ex.getCause().getResponseMessage();
					}
					exmap.put("getCause.getResponseMessage",""+cause_message);
					message.getHeaders().put("SoapFaultMessage", ex.getMessage() +": "+ ex.getCause().getResponseMessage());
				}
                
            }
            
            
            dumpProperties_TEXT_escaped("property.CamelExceptionCaught", exmap, sb_html);
            dumpProperties_TEXT("property.CamelExceptionCaught", exmap, sb_text);
        }
            
        }


//org.apache.cxf.headers.Header.list


		log.debug("processHeadersAndProperties: " +prefix_with_vars +"  SOAP_HEADERS_INFO01");
        
        enable = false;
        if ('YES'.equalsIgnoreCase(LOG_SOAP_HEADERS_INFO)) {
            enable = true;
        }
        
        if (enable) {

            def infomap = new HashMap();
            
            def headers = message.getHeaders();
            def list = headers.get("org.apache.cxf.headers.Header.list");
            if (list!=null) {
            
            	infomap.put("\${header.org.apache.cxf.headers.Header.list}", list);
            
            	infomap.put("size", list.size());
            	list.each { header -> 
            	
					//   elements of this head: com.sun.org.apache.xerces.internal.dom.ElementNSImpl
					
					//infomap.put("header["+header.getName()+"].object.clazz", header.getObject().getClass());
					infomap.put("header["+header.getName()+"]", header);
				 
					org.w3c.dom.Node element = (Node) header.getObject();
					def document = element.getOwnerDocument();
					//conversion using Transformer class | 
					def str1 = printXML(element);
            		infomap.put("header["+header.getName()+"].value", safeEscapeXml(str1));
            	}
            }
            
            
 	        dumpProperties_TEXT_escaped("SOAP Headers", infomap, sb_html);
            dumpProperties_TEXT("SOAP Headers", infomap, sb_text);
                   	      
       	   
       	    
	    }
                   

		log.debug("processHeadersAndProperties: " +prefix_with_vars +"  ATTACHMENT_INFO01");
        
        enable = false;
        if ('YES'.equalsIgnoreCase(LOG_ATTACHMENTS_INFO)) {
            enable = true;
        }
        
        if (enable) {

            def infomap = new HashMap();
            
	        def attachments = message.getAttachments();
       	    if ((attachments!=null)&&(!attachments.isEmpty())) {
       	      infomap.put("attachments",attachments);
       	      infomap.put("attachments.clazz.name",attachments.getClass().getName());
       	      infomap.put("attachments.keys",attachments.getOriginalMap().keySet());
       	      
       	      attachments.each { key -> infomap.put("attachments["+key.getKey()+"]", attachments.get(key.getKey()))  }
       	      
//       	      if ('YES'.equalsIgnoreCase(LOG_ATTACHMENTS_BODY)) {
//       	         attachments.each { key -> infomap.put("attachments.stream["+key.getKey()+"]", new String(readInputStream(attachments.get(key.getKey()).getInputStream())))  }
//       	      }
       	      
       	      dumpProperties_TEXT_escaped("Attachments", infomap, sb_html);
              dumpProperties_TEXT("Attachments", infomap, sb_text);
                   	      
       	    }
       	    
	    }
        
        log.debug("processHeadersAndProperties: " +prefix_with_vars +"  BODY_INFO01");
        
        enable = false;
        if ('YES'.equalsIgnoreCase(LOG_BODY_INFO)) {
            enable = true;
        }
        
        if (enable) {

            def body_test = message.getBody();
            def bodymap = new HashMap();
            
            bodymap.put("Body",body_test);
            
            if (body_test!=null) {
                bodymap.put("body.clazz.name",body_test.getClass().getCanonicalName());
            }
            
            dumpProperties_TEXT_escaped("Body", bodymap, sb_html);
            dumpProperties_TEXT("Body", bodymap, sb_text);
            

        }
        
        log.debug("processHeadersAndProperties: " +prefix_with_vars +"  BODY_OTHER01");
        
        enable = false;
        if ('YES'.equalsIgnoreCase(LOG_OTHER)) {
            enable = true;
        }
        
        if (enable) {
       
            def othermap = new HashMap();
            
            def currentDate = new Date();

            synchronized (currentDate){
                def timestamp = new java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS").format(currentDate);
            }
        
            othermap.put("CurrentTimestamp",timestamp);

            def lasttimestamp = message.getHeaders().get(PROPERTY_LAST_TIMESTAMP);
            
            if (lasttimestamp!=null) {
                  synchronized (lasttimestamp) {
                      def last_timestamp_str = new java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS").format(lasttimestamp);
                      othermap.put("LastTimestamp",last_timestamp_str);
                  }  
                  long diff = currentDate.getTime() - lasttimestamp.getTime();
                  synchronized (diff) {
                      def diff_str = new java.text.SimpleDateFormat("HH:mm:ss.SSS").format(diff);
                      othermap.put("CurrentTimestamp-LastTimestamp",diff_str);
                  }
            } 
            
            
            if (isTrue(MODIFY_EXCHANGE)) {
               message.setHeader(PROPERTY_LAST_TIMESTAMP, currentDate);
               message.setHeader(PROPERTY_LAST_TIMESTAMP+"_str", timestamp);
            } 
            
            
            //meta-inf/manifest.mf
            //OSGI-INF/blueprint/beans.xml
            //resources/parameters.prop
            
           //def bytes = this.getClass().getResource("/meta-inf/manifest.mf");
   			//def bytes = readInputStream(message.getClass().getClassLoader().getResource("META-INF/MANIFEST.MF").openConnection().getInputStream());
            //def bytes = readInputStream(message.getClass().getClassLoader().getResource("META-INF/MANIFEST.MF").openConnection().getInputStream());
            //def bytes = readInputStream(message.getClass().getResourceAsStream("./meta-inf/manifest.mf"));
           // def bytes = readInputStream(Thread.currentThread().getContextClassLoader().getResourceAsStream("META-INF/MANIFEST.MF"));
            //String bytes = new File("./src/main/resources/mapping/addLogItemsToConcurPayload.xsl").getText();
           //String bytes = new File("./META-INF/MANIFEST.MF").getText();
           //String file = new File(".");
           //StringBuffer sb = new StringBuffer();
           //sb.append("file.getName()");
          // def bytes = sb.toString().getBytes();
//           def fileContent = new String(bytes,"UTF-8");
//           othermap.put("bytes",fileContent);
           

            dumpProperties_TEXT_escaped("Other", othermap, sb_html);
            dumpProperties_TEXT("Other", othermap, sb_text);
                        
        }
        
        
        log.debug("processHeadersAndProperties: " +prefix_with_vars +"  BODY_CONFIG01");
        
        
        def props = message.getProperties();
        def property_ENABLE_MPL_LOGGING = props.get(PROPERTY_ENABLE_MPL_LOGGING);
        log.debug("processHeadersAndProperties: " +prefix_with_vars +"  BODY_CONFIG02:property_ENABLE_MPL_LOGGING="+ property_ENABLE_MPL_LOGGING);
        
        
        def property_ENABLE_FILE_LOGGING = props.get(PROPERTY_ENABLE_FILE_LOGGING);
        log.debug("processHeadersAndProperties: " +prefix_with_vars +"  BODY_CONFIG03:property_ENABLE_FILE_LOGGING="+ property_ENABLE_FILE_LOGGING);
        
        log.debug("processHeadersAndProperties: " +prefix_with_vars +"  BODY_CONFIG04:MPL_LOGGING_MODE="+ MPL_LOGGING_MODE);
        
        def mpl_enabled = false;
        if (isTrue(MPL_LOGGING_MODE)) {
            log.debug("processHeadersAndProperties: " +prefix_with_vars +"  BODY_CONFIG04a:mpl_enabled="+ mpl_enabled);
            mpl_enabled = true;
        } else if ("PROPERTY".equalsIgnoreCase(MPL_LOGGING_MODE)) {
            log.debug("processHeadersAndProperties: " +prefix_with_vars +"  BODY_CONFIG04b:MPL_LOGGING_MODE="+ MPL_LOGGING_MODE);
            if (isTrue(property_ENABLE_MPL_LOGGING)) {
                mpl_enabled = true;
                log.debug("processHeadersAndProperties: " +prefix_with_vars +"  BODY_CONFIG04c:mpl_enabled="+ mpl_enabled);
            }
        }
        log.debug("processHeadersAndProperties: " +prefix_with_vars +"  BODY_CONFIG05:mpl_enabled="+ mpl_enabled);
        
        
        def file_enabled = false;
        if (isTrue(FILE_LOGGING_MODE)) {
            file_enabled = true;
        } else if ("PROPERTY".equalsIgnoreCase(FILE_LOGGING_MODE)) {
            if (isTrue(property_ENABLE_FILE_LOGGING)) {
                file_enabled = true;
            }
        }
        log.debug("processHeadersAndProperties: " +prefix_with_vars +"  BODY_CONFIG05:file_enabled="+ file_enabled);
        
		def tmp_string = org.apache.commons.lang.StringEscapeUtils.escapeXml(sb_html.toString());
		
		sb_html.setLength(0);
		
        sb_html.append("<pre>");
		sb_html.append(tmp_string);
		sb_html.append("</pre>");
		
        log.debug("processHeadersAndProperties: " +prefix_with_vars +"  SAVE_MPL01");
        
        if (mpl_enabled) {
            def messageLog = messageLogFactory.getMessageLog(message);
            def mpl_prefix = prefix;
            if (prefix.startsWith(IFLOW_NAME+'_')) {
               mpl_prefix = prefix.substring(IFLOW_NAME.length()+1);
            }
            messageLog.addAttachmentAsString(mpl_prefix, sb_text.toString(), "text/plain");
        }
        
        log.debug("processHeadersAndProperties: " +prefix_with_vars +"  SAVE_FILE01");
        
        if (file_enabled) {
            ExecutorService pool = Executors.newSingleThreadExecutor();
            def task = {c -> pool.submit( c as Callable)}
            task{saveFile(""+prefix+".html", sb_html.toString().getBytes())};
        }
		log.debug("processHeadersAndProperties: " +prefix_with_vars +"  FINISH");
		
    } catch (Exception ex01) {
        log.error("processHeadersAndProperties: " +prefix_with_vars +" ",ex01)
        StringWriter sw = new StringWriter();
        ex01.printStackTrace(new PrintWriter(sw));
        log.info(sw.toString());
    }
    
}
public String safeClassName(Object obj) {
     if (obj == null) return "";
	 return obj.getClass().getName();	
}

public Object safeEscapeXml(Object payload) {
	if (payload instanceof java.lang.String) {
		return payload;
//		return org.apache.commons.lang.StringEscapeUtils.escapeXml(ex.getResponseBody());
	} else if (payload instanceof byte[]) {
		return payload;
//		return org.apache.commons.lang.StringEscapeUtils.escapeXml(ex.getResponseBody());
	} else if (payload instanceof Node) {
		return printXML(payload);
//		return org.apache.commons.lang.StringEscapeUtils.escapeXml(printXML(payload));
	}
    return payload;
}


public String printNode2(Node doc) throws IOException, TransformerException {
	def out = new ByteArrayOutputStream();
	TransformerFactory tf = TransformerFactory.newInstance();
	Transformer transformer = tf.newTransformer();
	transformer.setOutputProperty(OutputKeys.OMIT_XML_DECLARATION, "no");
	transformer.setOutputProperty(OutputKeys.METHOD, "xml");
	transformer.setOutputProperty(OutputKeys.INDENT, "yes");
	transformer.setOutputProperty(OutputKeys.ENCODING, "UTF-8");
	transformer.setOutputProperty("{http://xml.apache.org/xslt}indent-amount", "4");

	transformer.transform(new DOMSource(doc),
		 new StreamResult(new OutputStreamWriter(out, "UTF-8")));
	 def result = new String(out.toByteArray(), "UTF-8");
}


public byte[] readInputStream(InputStream inputStream) {

    if (inputStream==null) return null;
	def result = new ByteArrayOutputStream();
	def buffer = new byte[1024];
	def length;

    try {
        while ((length = inputStream.read(buffer)) != -1) {
		    result.write(buffer, 0, length);
	    }
	    def byteArray = result.toByteArray();
	    return byteArray;
    } catch (Exception ex) {
        log.error("readInputStream error",ex);
    }	
}

public void saveFile(String fileName, byte[] bytes) {
    try {
        def String METVIEWER_FOLDER = "metviewer";
        java.nio.file.Path path = Paths.get(METVIEWER_FOLDER+"/"+fileName);
        path.toFile().delete();
        path.getParent().toFile().mkdir();
        if (bytes!=null) {
            Files.write(path, bytes, StandardOpenOption.CREATE);
        } else {
            Files.write(path, "".getBytes(), StandardOpenOption.CREATE);
        }
    } catch (Exception ex) {
        log.error("saveFile.exception: filename:"+fileName+" ex:"+ex);
        throw new RuntimeException(ex);
    }
}




public void dumpProperties(String title, Map<String, Object> map, StringBuffer sb) {
    sb.append(title+"\n");
    for (String key : map.keySet()) {
        sb.append(key+"\t"+map.get(key)+"\n");
    }
}

public void dumpProperties_HTML(String title, Map<String, Object> map, StringBuffer sb) {
    sb.append("<h1>"+title+"</h1><br>\n");
    sb.append("<table>\n");
    for (String key : map.keySet()) {
        sb.append("<tr>\n");
        sb.append("<td>"+key+"</td><td>"+map.get(key)+"</td>\n");
        sb.append("</tr>\n");
    }
    sb.append("</table>\n");
}

public void dumpProperties_TEXT_escaped(String title, Map<String, Object> map, StringBuffer sb) {
	sb.append(safeEscapeXml(title)+"\n");
	for (String key : map.keySet()) {
		sb.append(String.format(" %-40s: %-40s\n",safeEscapeXml(key), safeEscapeXml(map.get(key))));
	}
	sb.append("\n");
}



public void dumpProperties_TEXT(String title, Map<String, Object> map, StringBuffer sb) {
    sb.append(title+"\n");
    for (String key : map.keySet()) {
        sb.append(String.format(" %-40s: %-40s\n",key, map.get(key)));
    }
    sb.append("\n");
}
