/* map the Ads response to Adwords format
 */
import com.sap.gateway.ip.core.customdev.util.Message;
import java.util.HashMap;
import groovy.json.*;
import groovy.xml.*;
def Message processData(Message message) {
       
    def body = message.getBody(java.lang.String); 
    def json = new JsonSlurper().parseText(body);
    
    // parse result create row list
    // -- list of attributes to be mapped
    //campaign:id, name
    //customer:timeZone, currencyCode
    //segments:date, device, adNetworkType, 
    //metrics:costMicros, impressions, clicks, videoViews, videoViewRate,invalidClicks
    // -- Target attributes
    //campaignID, campaign, timeZone, currency, day(?), device(?), networkWithSearchPartners(?), cost, impressions, clicks, views, viewRate,invalidClicks
    //--optional (new for migration to REST, pagination concept due to max row size)
    // nextPageToken
    def row = [:];
    def rowList = [];
    json.results.each{
        row = [:];
        if(it.campaign!=null){
            row = addToRow( row, 'campaign', it.campaign, 'name' );
            row = addToRow( row, 'campaignID', it.campaign, 'id',  );
            //row.campaign = it.campaign.name.toString();
            //row.campaignID = it.campaign.id.toString();
        }
        if(it.customer!=null){
            row = addToRow( row, 'timeZone', it.customer, 'timeZone' );
            row = addToRow( row, 'currency', it.customer, 'currencyCode' );
            //row.timeZone = it.customer.timeZone.toString() ;
            //row.currency= it.customer.currencyCode.toString() ;
        }
        if(it.segments!=null){
            row = addToRow( row, 'day', it.segments, 'date' );
            row = addToRow( row, 'device', it.segments, 'device' );
            row = addToRow( row, 'networkWithSearchPartners', it.segments, 'adNetworkType' );
            //row.day = it.segments.date.toString();
            //row.device = it.segments.device.toString();
            //row.networkWithSearchPartners = it.segments.adNetworkType.toString();
        }
        if(it.metrics!=null){
            row = addToRow( row, 'cost', it.metrics, 'costMicros' );
            row = addToRow( row, 'impressions', it.metrics, 'impressions' );
            row = addToRow( row, 'clicks', it.metrics, 'clicks' );
            row = addToRow( row, 'views', it.metrics, 'videoViews' );
            row = addToRow( row, 'viewRate', it.metrics, 'videoViewRate' );
            row = addToRow( row, 'invalidClicks', it.metrics, 'invalidClicks' );
            //row.cost=it.metrics.costMicros.toString();
            //row.impressions=it.metrics.impressions.toString();
            //row.clicks=it.metrics.clicks.toString();
            //row.views=it.metrics.videoViews.toString();
            //row.viewRate=it.metrics.videoViewRate.toString();
            //row.invalidClicks=it.metrics.invalidClicks.toString();
        }
        rowList.add(row);
    };
    
    //Prepare XML
    def writer = new StringWriter();
    def target = new MarkupBuilder(writer);
    target.mkp.xmlDeclaration(version: "1.0", encoding: "utf-8")
    
   // Following element will be added to xml
   //<report>
   // <report-name> - optional?
   // <date-range> - optional?
   // <table>
   //  <columns>
   //   <column name='xx' display='xx'/>
   //   ...
   //  </columns>
   //  <row campaignID='xx' campaign='xx' .../>
   //  ...
   // </table>
   // <nextPageToken> - newly added for pagination purpose
   //</report>
    target.report{ table{ 
        columns{
        String[] fields = json.fieldMask.toString().split(",");
         for( String value : fields ){
            switch (value){
                case "campaign.id":
                    column( name:"campaignID" , display:"Campaign ID")
                    break;
               case "campaign.name":
                    column( name:"campaign", display:"Campaign")
                    break;            
                case "customer.timeZone":
                    column( name:"timeZone", display:"Time zone")
                    break;
                case "customer.currencyCode":
                    column( name:"currency", display:"Currency")
                    break;
                case "segments.date": 
                    column( name:"day", display:"Day")
                    break;
                case "segments.device":
                    column( name:"device", display:"Device")
                    break;
                case "segments.adNetworkType":
                    column( name:"networkWithSearchPartners", display:"Network (with search partners)")
                    break;
                case "metrics.costMicros":
                    column( name:"cost", display:"Cost")
                    break;
                case "metrics.impressions":
                    column( name:"impressions", display:"Impressions")
                    break;            
                case "metrics.clicks":
                    column( name:"clicks", display:"Clicks")
                    break;
                case "metrics.videoViews":
                    column( name:"views", display:"Views")
                    break;
                case "metrics.videoViewRate":
                    column( name:"viewRate", display:"View rate")
                    break;
                case "metrics.invalidClicks":
                    column( name:"invalidClicks", display:"Invalid Clicks")
                    break;
                }
            }
                
        }                
        rowList.each{ 
            target.createNode('row', it);
            target.nodeCompleted('table','row'); // add closing tag for row
            }
        }
        if(json.nextPageToken != null){ // add next page token if it is from Google Ads response
            nextPageToken(json.nextPageToken)
        }        
    };

    
    message.setBody(writer.toString());
    
    return message;
}

def addToRow( Map row, String rowKey, Map resouces, String key ){
    if(resouces."$key"!=null){
    row."$rowKey" = resouces."$key";
    }
    return row;
}