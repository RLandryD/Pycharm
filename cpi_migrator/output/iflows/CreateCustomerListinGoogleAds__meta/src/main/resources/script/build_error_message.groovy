/* Map Google Ads error to fault error*/
import groovy.json.*;
import groovy.xml.*;
import com.sap.gateway.ip.core.customdev.util.Message;
import java.util.HashMap;

// Get Google API error and set as message body
def Message processData(Message message) {

    // get properties
    def map = message.getProperties();
    
    // get an exception java class instance
    def ex = map.get("CamelExceptionCaught");
    if (ex!=null) {
        
        // an http adapter throws an instance of org.apache.camel.component.ahc.AhcOperationFailedException
       if (ex.getClass().getCanonicalName().equals("org.apache.camel.component.ahc.AhcOperationFailedException")) {

            // Google Ads returns error information in Json
            /* Example
            {
              "error": {
                "code": 400,
                "message": "Request contains an invalid argument.",
                "status": "INVALID_ARGUMENT",
                "details": [
                  {
                    "@type": "type.googleapis.com/google.ads.googleads.v8.errors.GoogleAdsFailure",
                    "errors": [
                      {
                        "errorCode": {
                          "queryError": "UNRECOGNIZED_FIELD"
                        },
                        "message": "Unrecognized field in the query: 'campaign.ids'."
                      }
                    ],
                    "requestId": "DAGmiQASehxBOapQ90kf_A"
                  }
                ]
            }
            */
            try{
                def json = new JsonSlurper().parseText(ex.getResponseBody());
                if(json.error!=null){

                    def text = json.error.message.toString();
                    if(json.error.details[0].errors[0].message!=null){
                        text = text + json.error.details[0].errors[0].message.toString()
                    }
                    // set message body with error text
                    message.setBody(text);
                    return message;
                }
            } catch (Exception parseException){
                // catch the exception, let the last step to raise internal error
            }
                        
        }
    }
    // raise internal error when no Google API error is found
    throw new IllegalStateException(" Generic error is found; check message logs for details.");
    return message;
}

// not in use
def Message buildFaultMessage(Message message, String faultCode, String faultString){
    def writer = new StringWriter();
    def target = new MarkupBuilder(writer);
    target.'soap:Body'('xmlns:soap':"http://schemas.xmlsoap.org/soap/envelope/"){
    'soap:Fault'{
            'faultcode'(faultCode)
            'faultstring'( faultString )
        }
    };
    
    message.setBody(writer.toString());
    return message;
}