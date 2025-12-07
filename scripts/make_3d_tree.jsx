var TEMPLATE_PATH = "C:/Users/Egor/Documents/3dTreeAuto/data/auto 3D tree.pdf";
var SAVE_DIR = "D:/APKcompany Dropbox/Kirill Apkalikov/etsy/All Orders/3D Christmas tree/2025/"
var MAX_WIDTH = {
    "11": [30, 43, 53, 72, 81, 90, 90, 90, 90, 90, 90],
    "10": [30, 46, 56, 70, 75, 95, 100, 100, 100, 100],
    "9": [30, 36, 50, 63, 77, 93, 100, 100, 100],
    "8": [30, 39, 52, 66, 77, 102, 114, 114],
    "7": [30, 44, 56, 70, 85, 114, 116],
    "6": [30, 44, 60, 72, 87, 114],
    "5": [30, 56, 72, 92, 114],
    "4": [30, 62, 87, 97],
    "3": [30, 65, 82],
}

function collectText(p, out) {
    if (!out) out = [];
    if (p.textFrames) for (var i=0;i<p.textFrames.length;i++) out.push(p.textFrames[i]);
    if (p.legacyTextItems) for (var j=0;j<p.legacyTextItems.length;j++) out.push(p.legacyTextItems[j]);
    if (p.groupItems) for (var k=0;k<p.groupItems.length;k++) collectText(p.groupItems[k], out);
    if (p.layers) for (var m=0;m<p.layers.length;m++) collectText(p.layers[m], out);
    return out;
}

function getY(it) {
    if (it.position && it.position.length > 1) return it.position[1];
    if (it.geometricBounds) return it.geometricBounds[0];
    return 0;
}

function saveAsPDF(doc, outPath, artboards) {
  var f = new File(outPath);
  var o = new PDFSaveOptions();
  // o.compatibility = PDFCompatibility.ACROBAT8;
  // o.preserveEditability = false;
  // o.generateThumbnails = false;
  // o.viewAfterSaving = false;
  // if (artboards) {                    // e.g. "1" or "1-3" or "1,3,5"
  //   o.saveMultipleArtboards = true;
  //   o.artboardRange = artboards;
  // } else {
  //   o.saveMultipleArtboards = false;  // current artboard only
  // }
  doc.saveAs(f, o);
}

function main() {
    // read json file
    var nameFile=File('C:/Users/Egor/Documents/3dTreeAuto/data/tree_data.json');
    nameFile.open("r");
    var jsonData=nameFile.read();
    nameFile.close();
    var data=eval('(' + jsonData + ')')

    var layerName=data['layerName']
    var names=data['names']
    var filename=data['filename']


    var doc = app.open(File(TEMPLATE_PATH));
    try {
        var treeLayer = doc.layers.getByName(layerName);
    } catch (e) {
        return
    }
    treeLayer.visible = true;

    var textLayer = treeLayer.layers.getByName("text");
    var texts = collectText(textLayer, []);

    texts.sort(function(a,b){ return getY(b) - getY(a); }); // top → bottom by pos.y

    var len=Math.min(texts.length,names.length);
      for(var i=0;i<len;i++){
        var tf=texts[i];
        tf.contents=names[i];
        tf=tf.createOutline()

        var cap=MAX_WIDTH[layerName][i];
        if (cap && tf.width>cap){
          var gb=tf.geometricBounds, cx=(gb[1]+gb[3])/2, cy=(gb[0]+gb[2])/2;
          var s=(cap/tf.width)*100; if(s>100) s=100;               // shrink-to-fit only
          tf.resize(s,s,true,true,true,true,true,Transformation.CENTER);
          var gb2=tf.geometricBounds, ncx=(gb2[1]+gb2[3])/2, ncy=(gb2[0]+gb2[2])/2;
          // tf.translate(cx-ncx, cy-ncy);                            // keep center fixed
        }
  }

    try {
        treeLayer.groupItems.getByName("hide").hidden = true;
    } catch (e) {
        treeLayer.layers.getByName("hide").visible = false;
    }

    var OUT = SAVE_DIR + filename ;
    saveAsPDF(doc, OUT); 
    doc.close()
}

main();
